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
            Check("native choice gives the requested game-error instruction",
                native.Content?.ToString() == "ArkPie" && window.NativeHookNotes.Children.OfType<TextBlock>()
                    .Any(text => text.Text.Contains("检测到环境存在异常")));
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
                            Measure(width);
                            string context = theme + " " + width + " " + platform + " " + backend;
                            var connection = Bounds(window.ConnectionForm);
                            var observer = Bounds(window.ConnectionEventForm);
                            var apply = Bounds(applyBar);
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
