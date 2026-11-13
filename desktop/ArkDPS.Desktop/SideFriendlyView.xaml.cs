using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace ArkDPS.Desktop;

public partial class SideFriendlyView : UserControl
{
    private readonly ObservableCollection<RowItem> rows = new();
    private JsonElement view;
    private string battleId = "";
    private bool updating;

    public SideFriendlyView()
    {
        InitializeComponent();
        Rows.ItemsSource = rows;
        Details.UsePrimaryDataOnly();
    }

    internal void Update(string currentBattleId, JsonElement ranking)
    {
        view = ranking;
        bool newBattle = currentBattleId != battleId;
        if (newBattle)
        {
            battleId = currentBattleId;
            Details.Reset();
            DetailViewport.ScrollToTop();
        }
        UpdateRows(holdOrder: !newBattle && Rows.IsMouseOver, reset: newBattle);
        if (newBattle && rows.Count > 0) Rows.ScrollIntoView(rows[0]);
        Details.Update(view);
    }

    internal void RefreshAppearance()
    {
        Details.CompactReference = true;
        UpdateRows(holdOrder: Rows.IsMouseOver);
    }

    private void UpdateRows(bool holdOrder, bool reset = false)
    {
        updating = true;
        try
        {
            if (reset) rows.Clear();
            var incoming = view.Get("entries").Items().ToArray();
            var ids = incoming.Select(entry => entry.Text("owner_id")).ToHashSet();
            foreach (var row in rows.Where(row => !ids.Contains(row.Id)).ToArray())
                rows.Remove(row);

            var byId = rows.ToDictionary(row => row.Id);
            var share = SharePresentation.From(view);
            for (int index = 0; index < incoming.Length; index++)
            {
                var entry = incoming[index];
                string id = entry.Text("owner_id");
                if (!byId.TryGetValue(id, out var row))
                {
                    row = new RowItem { Entry = entry, ShareInfo = share };
                    rows.Add(row);
                    byId.Add(id, row);
                }
                else
                {
                    row.ShareInfo = share;
                    row.Entry = entry;
                }

                if (!holdOrder)
                {
                    int current = rows.IndexOf(row);
                    if (current != index) rows.Move(current, index);
                }
            }

            if (Rows.SelectedItem is null && Details.Owner is string pinned && byId.TryGetValue(pinned, out var selected))
                Rows.SelectedItem = selected;
            string empty = view.Text("empty_text");
            EmptyMessage.Text = empty.Length > 0 ? empty : "等待战斗数据";
            EmptyMessage.Visibility = rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        }
        finally { updating = false; }
    }

    private void SelectOperator(object sender, SelectionChangedEventArgs e)
    {
        if (!updating && Rows.SelectedItem is RowItem row)
            Details.Pin(row.Id, view);
    }

    private void ReleaseOrder(object sender, MouseEventArgs e) => UpdateRows(holdOrder: false);

    private void FitDetails(object sender, SizeChangedEventArgs e)
    {
        DetailViewport.MaxHeight = Math.Max(0, e.NewSize.Height * 0.56 - DetailViewport.Margin.Top);
    }
}
