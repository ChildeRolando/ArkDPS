using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace ArkDPS.Desktop;

public partial class SideFriendlyView : UserControl
{
    private string battleId = "";

    public SideFriendlyView()
    {
        InitializeComponent();
        RankingPanel.ConfigureSideRanking();
        RankingPanel.PinRequested = id => Details.Pin(id, RankingPanel.View);
        Details.UsePrimaryDataOnly();
    }

    internal void ConfigureCommands(Func<string,Dictionary<string,object?>?,Task> command) =>
        RankingPanel.Command=command;

    internal void SetConfigurationAvailable(bool available) =>
        RankingPanel.SetConfigurationAvailable(available);

    internal void Update(string currentBattleId, JsonElement ranking, NativePcReadiness readiness = default)
    {
        bool reset = currentBattleId != battleId || ranking.Get("config").Text("panel_id") != RankingPanel.PanelId;
        battleId = currentBattleId;
        if (reset)
        {
            RankingPanel.ClearSelection();
            Details.Reset();
            DetailViewport.ScrollToTop();
        }
        RankingPanel.Update(ranking);
        if (readiness.Active && RankingPanel.Rows.Items.Count == 0)
        {
            RankingPanel.Empty.Text = readiness.Label;
            RankingPanel.Empty.Visibility = Visibility.Visible;
        }
        RankingPanel.RestoreSideSelection(Details.Owner, reset);
        Details.CompactReference = true;
        Details.Update(ranking);
    }

    private void FitDetails(object sender, SizeChangedEventArgs e)
    {
        DetailViewport.MaxHeight = Math.Max(0, e.NewSize.Height * 0.56 - DetailViewport.Margin.Top);
    }
}
