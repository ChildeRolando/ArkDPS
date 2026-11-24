using System;
using System.Reflection;
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
                native.Content?.ToString() == "自研（若遇“检测到环境存在异常”报错选择此选项）");
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
