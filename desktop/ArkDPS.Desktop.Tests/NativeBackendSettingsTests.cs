using System;
using System.Reflection;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using ArkDPS.Desktop;

internal static class NativeBackendSettingsTests
{
    internal static void Run()
    {
        var window = new MainWindow();
        typeof(MainWindow).GetField("initialized", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(window, true);
        int passed = 0;
        void Check(string name, bool condition)
        {
            if (!condition) throw new InvalidOperationException(name);
            passed++;
        }
        try
        {
            window.ConnectionPlatform.SelectedIndex = 1;
            Check("standard package retains selectable platform and PC backend",
                window.ConnectionPlatform.IsEnabled && window.NativeEventBackend.IsEnabled);
            Check("Frida remains the default PC selection", window.NativeEventBackend.SelectedIndex == 0);
            window.NativeEventBackend.SelectedIndex = 1;
            var native = (ComboBoxItem)window.NativeEventBackend.SelectedItem;
            Check("both PC backend pages and dropdown choices share the game-error instruction",
                native.Content?.ToString() == "ArkPie"
                && window.NativeEventBackendHint.Text == "遇到“检测到环境存在异常”报错时，选择 ArkPie。"
                && ReferenceEquals(window.NativeEventBackendHint.Parent, window.NativeEventNotes)
                && window.NativeEventBackend.ToolTip?.ToString() == window.NativeEventBackendHint.Text
                && window.NativeEventBackend.Items.Cast<ComboBoxItem>()
                    .All(item => item.ToolTip?.ToString() == window.NativeEventBackendHint.Text));
            Check("native choice shows startup guidance and hides unused file source",
                window.NativeHookNotes.Visibility == Visibility.Visible
                && window.FridaEventNotes.Visibility == Visibility.Collapsed
                && window.NativeEventFeedFields.Visibility == Visibility.Collapsed);
            window.NativeEventFeed.Text = @"C:\existing-feed.jsonl";
            window.NativeEventBackend.SelectedIndex = 0;
            window.NativeEventBackend.SelectedIndex = 1;
            Check("an existing cooperative source can still be edited",
                window.NativeEventFeedFields.Visibility == Visibility.Visible);
            window.NativeEventBackend.SelectedIndex = 0;
            Check("switching to Frida restores its own guidance",
                window.FridaEventNotes.Visibility == Visibility.Visible
                && window.NativeHookNotes.Visibility == Visibility.Collapsed
                && window.NativeEventFeedFields.Visibility == Visibility.Collapsed);
            Check("release UI has no candidate title", !window.Title.Contains("测试"));
            Check("observer toggle uses the requested name", window.EventsEnabled.Content?.ToString() == "启用战斗事件观察者");
            Check("current state uses prominent typography", window.ConnectionStatusSummary.FontSize >= 15
                && ((StackPanel)window.ConnectionStatusSummary.Parent).Children.OfType<TextBlock>()
                    .Any(text => text.Text == "当前状态" && text.FontSize >= 18));
            Check("technical details require deliberate expansion", window.ConnectionDiagnostics.Parent is Expander { IsExpanded: false });
            window.NativeEventFeed.Text = "";
            window.Navigate("settings").GetAwaiter().GetResult();
            var settingsGrid = (Grid)window.SettingsPage.Content;
            Check("settings exposes vertical scrolling while keeping horizontal layout within the viewport",
                window.SettingsPage.VerticalScrollBarVisibility == ScrollBarVisibility.Auto &&
                window.SettingsPage.HorizontalScrollBarVisibility == ScrollBarVisibility.Disabled);
            var applyBar = settingsGrid.Children.OfType<Border>().Single(card => Grid.GetRow(card) == 2);
            var displayCard = settingsGrid.Children.OfType<Border>().Single(card => Grid.GetRow(card) == 3);
            var diagnosticsCard = settingsGrid.Children.OfType<Border>().Single(card => Grid.GetRow(card) == 4);
            var arrange = typeof(MainWindow).GetMethod("ArrangePanels", BindingFlags.Instance | BindingFlags.NonPublic)!;
            Rect Bounds(FrameworkElement element) => element.TransformToAncestor(settingsGrid)
                .TransformBounds(new Rect(element.RenderSize));
            void Measure(double width)
            {
                window.ContentRoot.Measure(new Size(width, 900));
                window.ContentRoot.Arrange(new Rect(0, 0, width, 900));
                arrange.Invoke(window, null);
                window.ContentRoot.UpdateLayout();
            }
            bool BottomsAlign(double width) => width < 1000
                || Math.Abs(Bounds(window.ConnectionForm).Bottom - Bounds(window.ConnectionEventForm).Bottom) <= 1;
            bool NaturalObserverHeight(double width) => width >= 1000
                || Math.Abs(window.ConnectionEventForm.ActualHeight
                    - (window.ConnectionEventForm.DesiredSize.Height - window.ConnectionEventForm.Margin.Top
                        - window.ConnectionEventForm.Margin.Bottom)) <= 1;
            bool HiddenUpdatesCollapse(double width, double visibleConnectionTop, string context)
            {
                window.ReleaseSettings.Visibility = Visibility.Collapsed;
                Measure(width);
                var hiddenConnection = Bounds(window.ConnectionForm);
                var leftColumn = Bounds(window.SettingsConnectionColumn);
                bool collapsed = hiddenConnection.Top < visibleConnectionTop - 1
                    && Math.Abs(hiddenConnection.Top - leftColumn.Top) <= 1;
                Check(context + " hidden updates retain wide card bottom alignment", BottomsAlign(width));
                Check(context + " narrow observer uses natural height when updates hide", NaturalObserverHeight(width));
                window.ReleaseSettings.Visibility = Visibility.Visible;
                Measure(width);
                return collapsed;
            }
            string originalTheme = Theme.Key;
            try
            {
                foreach (string theme in new[] { "lone-trail", "people", "babel" })
                {
                    Theme.Apply(theme);
                    foreach (double width in new[] { 560d, 900d, 1672d })
                    {
                        foreach (var (platform, backend) in new[] { (0, 0), (1, 0), (1, 1) })
                        {
                            window.ConnectionPlatform.SelectedIndex = platform;
                            window.NativeEventBackend.SelectedIndex = backend;
                            window.ReleaseSettings.Visibility = Visibility.Visible;
                            Measure(width);
                            string context = theme + " " + width + " " + platform + " " + backend;
                            var connection = Bounds(window.ConnectionForm);
                            var observer = Bounds(window.ConnectionEventForm);
                            var apply = Bounds(applyBar);
                            var updates = Bounds(window.ReleaseSettingsCard);
                            var connectionColumn = Bounds(window.SettingsConnectionColumn);
                            Check(context + " updates occupy the top of the left column above connection",
                                Math.Abs(updates.Top - connectionColumn.Top) <= 1 && updates.Bottom <= connection.Top);
                            Check(context + " both left-column cards stay clear of the observer",
                                connectionColumn.Bottom <= observer.Top + 1 || connectionColumn.Right <= observer.Left + 1);
                            Check(context + " hiding updates removes their occupied space",
                                HiddenUpdatesCollapse(width, connection.Top, context));
                            connection = Bounds(window.ConnectionForm);
                            observer = Bounds(window.ConnectionEventForm);
                            apply = Bounds(applyBar);
                            Check(context + " connection and observer cards never overlap",
                                !connection.IntersectsWith(observer));
                            Check(context + " shared action follows both input groups",
                                apply.Top >= Math.Max(connection.Bottom, observer.Bottom));
                            Check(context + " display and diagnostic tools follow connection apply",
                                Bounds(displayCard).Top >= apply.Bottom && Bounds(diagnosticsCard).Top >= Bounds(displayCard).Bottom);
                            Check(context + " narrow layouts stack observer below connection",
                                width >= 1000 || observer.Top >= connection.Bottom);
                            Check(context + " desktop layouts align connection groups side by side",
                                width < 1000 || observer.Left >= connection.Right);
                            Check(context + " wide connection and observer card bottoms align", BottomsAlign(width));
                            Check(context + " narrow observer uses natural height", NaturalObserverHeight(width));
                            Check(context + " settings stays within its scroll viewport",
                                settingsGrid.ActualWidth <= window.SettingsPage.ViewportWidth + 1 && window.SettingsPage.ScrollableWidth <= 1);
                            if (platform == 0) continue;
                            var notes = backend == 0 ? window.FridaEventNotes : window.NativeHookNotes;
                            var legend = notes.Children.OfType<Expander>().Single();
                            legend.IsExpanded = true;
                            Measure(width);
                            var meaning = (Grid)legend.Content;
                            var titles = meaning.Children.OfType<TextBlock>().Where(text => Grid.GetColumn(text) == 0).ToArray();
                            var descriptions = meaning.Children.OfType<TextBlock>().Where(text => Grid.GetColumn(text) == 1).ToArray();
                            Check(context + " legend provides separate semantic status rows",
                                titles.Length == 7 && descriptions.Length == 7 && titles.Select(Grid.GetRow).Distinct().Count() == 7);
                            Check(context + " expanded state legend remains before the shared action",
                                Bounds(applyBar).Top >= Bounds(window.ConnectionEventForm).Bottom);
                            Check(context + " expanded legend retains wide card bottom alignment", BottomsAlign(width));
                            Check(context + " expanded legend still collapses hidden update space",
                                HiddenUpdatesCollapse(width, Bounds(window.ConnectionForm).Top, context + " expanded"));
                            legend.IsExpanded = false;
                        }
                    }
                }
            }
            finally { Theme.Apply(originalTheme); }
        }
        finally
        {
            typeof(MainWindow).GetField("quitting", BindingFlags.Instance | BindingFlags.NonPublic)!
                .SetValue(window, true);
            window.Close();
            ((IDisposable)typeof(MainWindow).GetField("recoveryCancellation", BindingFlags.Instance | BindingFlags.NonPublic)!
                .GetValue(window)!).Dispose();
        }
        Console.WriteLine($"PASS_NATIVE_BACKEND_SETTINGS {passed} assertions (injected controls; no windows/input/targets)");
    }
}
