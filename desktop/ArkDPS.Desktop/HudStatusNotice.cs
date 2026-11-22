using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace ArkDPS.Desktop;

internal readonly record struct HudDataStatus(string Code, string Label, bool Retained, bool Unavailable)
{
    internal string Notice => Retained || Unavailable || Code is "stale" or "disconnected" or "reconnecting" or "locating" or "frozen" or "preparing" or "sampling_rejected"
        ? string.IsNullOrWhiteSpace(Label)
            ? Retained ? "上次快照 · 等待当前战局" : "统计暂不可用 · 保留最后有效记录"
            : Label
        : "";
}

/// <summary>A warning only; healthy HUDs keep their existing layout.</summary>
internal sealed class HudStatusNotice : Border
{
    private readonly TextBlock message = new()
    {
        FontSize = 11, TextWrapping = TextWrapping.Wrap,
        IsHitTestVisible = false, Focusable = false,
    };

    internal HudStatusNotice()
    {
        Visibility = Visibility.Collapsed;
        IsHitTestVisible = false;
        Focusable = false;
        Padding = new Thickness(6, 3, 6, 3);
        BorderThickness = new Thickness(0, 0, 0, 1);
        SetResourceReference(BackgroundProperty, "ConsoleCard");
        SetResourceReference(BorderBrushProperty, "ConsoleLine");
        message.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleInk");
        Child = message;
        AutomationProperties.SetAutomationId(this, "HudDataStatus");
    }

    internal string Message => message.Text;

    internal bool Update(HudDataStatus status)
    {
        string text = status.Notice;
        if (message.Text == text) return false;
        message.Text = text;
        Visibility = message.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        AutomationProperties.SetName(this, message.Text);
        return true;
    }
}
