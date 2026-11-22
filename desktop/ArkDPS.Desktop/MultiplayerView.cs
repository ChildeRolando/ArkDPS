using System;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Markup;

namespace ArkDPS.Desktop;

// This surface only presents worker-owned statistics. Choosing a player never
// changes the ordinary live panel configuration, history selection or HUD.
public sealed class MultiplayerView : ScrollViewer
{
    private readonly TextBlock notice = Label("", 13, "Muted");
    private readonly TextBlock scope = Label("", 12, "Muted");
    private readonly TextBlock selection = Label("", 16, "Ink", true);
    private readonly TextBlock identity = Label("", 12, "Muted");
    private readonly UniformGrid comparisons = new() { Columns = 4 };
    private readonly Grid detail = new();
    private readonly PlayerButton[] players;
    private readonly StatisticPanel ranking = new();
    private readonly Inspector inspector = new();
    private JsonElement snapshot;
    private string scopeIdentity = "";
    private string? selectedPlayerUid;
    private bool defenseAvailable;

    internal string? SelectedPlayerUid => selectedPlayerUid;
    internal StatisticPanel Ranking => ranking;
    internal Inspector DetailInspector => inspector;

    public MultiplayerView()
    {
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
        HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled;
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        Margin = new Thickness(12, 0, 12, 8);
        AutomationProperties.SetName(this, "卫戍四家统计");
        AutomationProperties.SetAutomationId(this, "MultiplayerPage");
        var body = new StackPanel();
        Content = body;
        notice.Margin = new Thickness(0, 0, 0, 8);
        notice.TextWrapping = TextWrapping.Wrap;
        scope.Margin = new Thickness(0, 0, 0, 12);
        scope.TextWrapping = TextWrapping.Wrap;
        AutomationProperties.SetAutomationId(notice, "MultiplayerNotice");
        body.Children.Add(notice);
        body.Children.Add(scope);
        players = Enumerable.Range(1, 4).Select(slot => new PlayerButton(slot, SelectPlayer)).ToArray();
        foreach (var player in players) comparisons.Children.Add(player);
        comparisons.Margin = new Thickness(-3, 0, -3, 14);
        body.Children.Add(comparisons);
        selection.Margin = new Thickness(0, 0, 0, 4);
        identity.Margin = new Thickness(0, 0, 0, 10);
        identity.TextWrapping = TextWrapping.Wrap;
        AutomationProperties.SetAutomationId(identity, "MultiplayerSelectedIdentity");
        body.Children.Add(selection);
        body.Children.Add(identity);
        detail.ColumnDefinitions.Add(new ColumnDefinition());
        detail.ColumnDefinitions.Add(new ColumnDefinition());
        detail.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        detail.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        ranking.MinWidth = 0;
        ranking.VerticalAlignment = VerticalAlignment.Top;
        inspector.VerticalAlignment = VerticalAlignment.Top;
        inspector.CompactReference = true;
        ranking.ConfigureActorRanking();
        ranking.PinRequested = owner =>
        {
            inspector.Pin(owner, ranking.View);
            UpdateIdentity(owner);
        };
        AutomationProperties.SetAutomationId(ranking, "MultiplayerOperatorRanking");
        AutomationProperties.SetName(ranking.Rows, "所选玩家的干员排行");
        AutomationProperties.SetAutomationId(inspector, "MultiplayerOperatorInspector");
        detail.Children.Add(ranking);
        detail.Children.Add(inspector);
        body.Children.Add(detail);
        SizeChanged += (_, _) => ArrangeComparison();
        Update(default);
    }

    internal void Update(JsonElement value)
    {
        snapshot = value;
        string kind = value.Text("scope_kind");
        string scopeId = value.Get("scope").ValueKind == JsonValueKind.String ? value.Text("scope") : "";
        bool eligible = kind == "defense" && value.Bool("available") && !string.IsNullOrWhiteSpace(scopeId);
        string nextScope = kind + "\u001f" + value.Text("native_round") + "\u001f" + scopeId;
        bool scopeChanged = scopeIdentity != nextScope;
        bool enteringDefense = eligible && !defenseAvailable;
        scopeIdentity = nextScope;
        defenseAvailable = eligible;
        var incoming = eligible ? value.Get("players").Items().ToArray() : Array.Empty<JsonElement>();
        var known = incoming.Where(player => KnownPlayer(player, incoming)).ToArray();
        if (scopeChanged || enteringDefense || !eligible)
        {
            selectedPlayerUid = eligible ? PlayerUid(known.FirstOrDefault(player => player.Bool("is_local")))
                ?? PlayerUid(known.OrderBy(player => player.Number("slot")).FirstOrDefault()) : null;
            ResetOperator();
        }
        else if (selectedPlayerUid is not null && !known.Any(player => PlayerUid(player) == selectedPlayerUid))
        {
            selectedPlayerUid = null;
            ResetOperator();
        }
        string note = value.Text("note");
        notice.Text = eligible
            ? note.Length > 0 ? note : "防御阶段 · 按真实玩家 UID 分别查看输出。选择玩家，再选择干员查看详情。"
            : EmptyNotice(kind == "defense" && scopeId.Length == 0 ? "unknown" : kind);
        if (eligible) notice.Text = value.Text("status") switch
        {
            "gap" => "观察已中断 · 以下保留中断前已观察数据。" + notice.Text,
            "ended" => "本轮防御已结束 · " + notice.Text,
            _ => notice.Text,
        };
        scope.Text = eligible ? "防御四家：已观察结算伤害（含过量） / 观察时段平均 DPS。干员排行：已观察有效伤害 / 接敌 DPS。协防数据单独统计。" : "";
        comparisons.Visibility = selection.Visibility = identity.Visibility = detail.Visibility = scope.Visibility =
            eligible ? Visibility.Visible : Visibility.Collapsed;
        foreach (var player in players)
        {
            var matches = incoming.Where(item => item.Number("slot") == player.Slot).ToArray();
            var item = matches.Length == 1 ? matches[0] : default;
            bool knownIdentity = KnownPlayer(item, incoming);
            player.Update(item, knownIdentity && PlayerUid(item) == selectedPlayerUid, knownIdentity);
        }
        UpdateSelected();
        ArrangeComparison();
    }

    internal void SelectPlayer(string playerUid)
    {
        if (!defenseAvailable) return;
        var incoming = snapshot.Get("players").Items().ToArray();
        if (!incoming.Any(player => PlayerUid(player) == playerUid && KnownPlayer(player, incoming))) return;
        if (selectedPlayerUid != playerUid)
        {
            selectedPlayerUid = playerUid;
            ResetOperator();
        }
        Update(snapshot);
    }

    private static string EmptyNotice(string kind) => kind switch
    {
        "assist" => "当前为协防阶段，协防数据单独统计；四家防御 DPS 暂不显示。",
        "boss" => "当前为首领阶段，四家防御 DPS 暂不显示。",
        "prepare" => "当前处于准备阶段，等待进入防御阶段后查看四家 DPS。",
        "defense" => "当前防御数据尚不可用，等待可核实的玩家与事件证据。",
        _ => "尚未取得卫戍协议原生阶段证据，当前阶段未知；等待事件观察。仅在确认防御阶段后显示四家 DPS。",
    };

    private static string? PlayerUid(JsonElement player) => player.Get("player_uid").ValueKind == JsonValueKind.String
        && !string.IsNullOrWhiteSpace(player.Text("player_uid")) ? player.Text("player_uid") : null;

    private static bool KnownPlayer(JsonElement player, JsonElement[] incoming) => PlayerUid(player) is { } uid
        && player.Get("slot").ValueKind == JsonValueKind.Number
        && player.Get("slot").TryGetInt32(out int slot) && slot is >= 1 and <= 4
        && incoming.Count(item => PlayerUid(item) == uid) == 1
        && incoming.Count(item => item.Number("slot") == slot) == 1;

    private static string PlayerLabel(JsonElement player) => PlayerUid(player) is { } uid
        ? (player.Text("player_name") is { Length: > 0 } name ? name + " · " : "") + "UID " + uid
        : "玩家身份未知";

    private void ResetOperator()
    {
        ranking.ClearSelection();
        inspector.Reset();
    }

    private void UpdateSelected()
    {
        var player = defenseAvailable && selectedPlayerUid is not null
            ? snapshot.Get("players").Items().FirstOrDefault(item => PlayerUid(item) == selectedPlayerUid) : default;
        selection.Text = !defenseAvailable ? "" : selectedPlayerUid is null
            ? "请选择已确认身份的玩家" : PlayerLabel(player) + " · 干员排行";
        var view = player.Get("view");
        // Losing a player's view clears the old UID immediately. The inspector
        // intentionally keeps ordinary live pins across missing rows elsewhere.
        if (view.ValueKind != JsonValueKind.Object || view.Get("source").ValueKind != JsonValueKind.Object)
            ResetOperator();
        else if (inspector.Owner is { } owner && view.Get("source").Get(owner).ValueKind != JsonValueKind.Object)
            ResetOperator();
        ranking.Update(view, false);
        if (ranking.Heading.Text.Length == 0) ranking.Heading.Text = "已观察有效伤害 · 接敌 DPS";
        if (ranking.Rows.Items.Count == 0)
        {
            ranking.Empty.Text = player.Text("status") == "unknown"
                ? "这家的身份或事件覆盖未知，暂无可核实的干员数据。"
                : "等待这家可归属的干员伤害事件。";
            ranking.Empty.Visibility = Visibility.Visible;
        }
        inspector.Update(view);
        if (defenseAvailable) UpdateIdentity(inspector.Owner);
        else identity.Text = "";
    }

    private void UpdateIdentity(string? owner)
    {
        if (owner is null)
        {
            identity.Text = "干员按战斗 UID 分开记录。选择排行中的干员查看对应数据。";
            return;
        }
        var row = ranking.View.Get("source").Get(owner);
        string uid = row.Text("actor_uid");
        identity.Text = "已选择战斗 UID " + (uid.Length > 0 ? uid : owner);
    }

    private void ArrangeComparison()
    {
        double width = ActualWidth;
        if (width <= 0) return;
        comparisons.Columns = width >= 980 ? 4 : 2;
        bool stacked = width < 920;
        detail.ColumnDefinitions[0].Width = new GridLength(stacked ? 1 : 1.18, GridUnitType.Star);
        detail.ColumnDefinitions[1].Width = stacked ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        Grid.SetColumn(inspector, stacked ? 0 : 1);
        Grid.SetRow(inspector, stacked ? 1 : 0);
        ranking.Margin = new Thickness(0, 0, stacked ? 0 : 10, stacked ? 10 : 0);
        ranking.Rows.MaxHeight = stacked ? 340 : 520;
    }

    private static TextBlock Label(string text, double size, string token, bool bold = false)
    {
        var label = new TextBlock { Text = text, FontSize = size, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal };
        label.SetResourceReference(TextBlock.ForegroundProperty, token);
        return label;
    }

    private sealed class PlayerButton : Button
    {
        // Unavailable identities remain readable data. Keep the product button
        // vocabulary without fading the card's readings or its surface.
        private static readonly ControlTemplate PlayerTemplate = (ControlTemplate)XamlReader.Parse("""
            <ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" TargetType="Button">
             <Border x:Name="edge" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
               Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
               BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="{DynamicResource ControlRadius}" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Stretch" VerticalAlignment="Top"/>
             </Border>
             <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="edge" Property="BorderBrush" Value="{DynamicResource Accent}"/></Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="edge" Property="BorderBrush" Value="{DynamicResource Focus}"/><Setter TargetName="edge" Property="BorderThickness" Value="2"/></Trigger>
              <Trigger Property="IsPressed" Value="True"><Setter TargetName="edge" Property="Opacity" Value="0.82"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter Property="Cursor" Value="Arrow"/></Trigger>
             </ControlTemplate.Triggers>
            </ControlTemplate>
            """);
        internal int Slot { get; }
        private string? playerUid;
        private readonly TextBlock title = Label("", 16, "Ink", true);
        private readonly TextBlock accountIdentity = Label("UID 未知", 12, "Muted");
        private readonly TextBlock state = Label("", 12, "Muted");
        private readonly TextBlock[] captions = new TextBlock[3];
        private readonly TextBlock[] readings = new TextBlock[3];
        private static readonly (string Key, string Label)[] Metrics =
            { ("damage", "结算伤害"), ("dps", "时段平均 DPS"), ("recent_dps", "近 5 秒 DPS") };

        internal PlayerButton(int slot, Action<string> select)
        {
            Slot = slot;
            SetResourceReference(StyleProperty, typeof(Button));
            Template = PlayerTemplate;
            Margin = new Thickness(3, 0, 3, 6);
            Padding = new Thickness(12, 10, 12, 11);
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Top;
            AutomationProperties.SetAutomationId(this, "MultiplayerPlayer" + slot);
            ToolTipService.SetShowsToolTipOnKeyboardFocus(this, true);
            ToolTipService.SetShowOnDisabled(this, true);
            var body = new StackPanel();
            title.TextWrapping = TextWrapping.Wrap;
            accountIdentity.TextWrapping = TextWrapping.Wrap;
            accountIdentity.Margin = new Thickness(0, 3, 0, 0);
            accountIdentity.SetResourceReference(TextBlock.FontFamilyProperty, "DataFont");
            state.TextWrapping = TextWrapping.NoWrap;
            state.TextTrimming = TextTrimming.CharacterEllipsis;
            state.Margin = new Thickness(0, 3, 0, 10);
            body.Children.Add(title);
            body.Children.Add(accountIdentity);
            body.Children.Add(state);
            for (int index = 0; index < Metrics.Length; index++)
            {
                var row = new Grid { Margin = new Thickness(0, 3, 0, 0) };
                row.ColumnDefinitions.Add(new ColumnDefinition());
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                captions[index] = Label(Metrics[index].Label, 12, "Muted");
                readings[index] = Label("未知", 16, "Ink", true);
                readings[index].Margin = new Thickness(8, 0, 0, 0);
                readings[index].SetResourceReference(TextBlock.FontFamilyProperty, "DataFont");
                Grid.SetColumn(readings[index], 1);
                row.Children.Add(captions[index]);
                row.Children.Add(readings[index]);
                body.Children.Add(row);
            }
            Content = body;
            Click += (_, _) => { if (playerUid is not null) select(playerUid); };
        }

        internal void Update(JsonElement player, bool selected, bool knownIdentity)
        {
            playerUid = knownIdentity ? PlayerUid(player) : null;
            IsEnabled = knownIdentity;
            title.Text = knownIdentity ? (player.Text("player_name") is { Length: > 0 } name ? name : "玩家")
                + (player.Bool("is_local") ? " · 我方" : "") : "玩家身份未知";
            accountIdentity.Text = playerUid is not null ? "UID " + playerUid : "UID 未知";
            string status = player.Text("status");
            string coverage = player.Get("display").Text("coverage");
            state.Text = !knownIdentity ? "等待可靠的玩家身份" : coverage.Length > 0 ? coverage : status switch
            {
                "complete" => "完整覆盖", "partial" => "部分覆盖", "observed" => "已观察 · 完整性未知",
                "waiting" => "等待事件", "unknown" => "身份或事件未知", "unavailable" => "数据不可用",
                _ => player.Bool("coverage_complete") ? "完整覆盖" : "覆盖未知",
            };
            var descriptions = new string[Metrics.Length];
            for (int index = 0; index < Metrics.Length; index++)
            {
                var metric = Metrics[index];
                bool exact = knownIdentity && IsNumber(player.Get(metric.Key));
                string key = exact ? metric.Key : "observed_" + metric.Key;
                bool known = knownIdentity && IsNumber(player.Get(key));
                captions[index].Text = metric.Label;
                string formatted = player.Get("display").Text(metric.Key);
                readings[index].Text = known ? (formatted.Length > 0 ? formatted : (!exact ? "已观察 " : "") + player.Ui(key)) : "未知";
                descriptions[index] = captions[index].Text + " " + readings[index].Text;
            }
            SetResourceReference(BackgroundProperty, selected ? "Inset" : "Card");
            SetResourceReference(BorderBrushProperty, selected ? "Accent" : "Line");
            BorderThickness = new Thickness(selected ? 2 : 1);
            string reasons = string.Join("；", player.Get("reasons").Items().Select(item => item.Text()).Where(text => text.Length > 0));
            string description = string.Join(" · ", descriptions);
            string window = player.Text("window_text");
            ToolTip = title.Text + " · " + accountIdentity.Text + " · " + state.Text + "\n" + description + (window.Length > 0 ? "\n" + window : "") + (reasons.Length > 0 ? "\n" + reasons : "");
            AutomationProperties.SetName(this, title.Text + "，" + accountIdentity.Text + "，" + state.Text + "，" + description + (selected ? "，已选择" : knownIdentity ? "，选择查看干员" : ""));
            AutomationProperties.SetHelpText(this, reasons);
        }

        private static bool IsNumber(JsonElement value) => value.ValueKind == JsonValueKind.Number &&
            value.TryGetDouble(out double number) && double.IsFinite(number);
    }
}
