using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Interop;

namespace ArkDPS.Desktop;

internal static class Native
{
    [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll", SetLastError=true)] private static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll", SetLastError=true)] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll", EntryPoint="GetWindowLongPtrW", SetLastError=true)] private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint="SetWindowLongPtrW", SetLastError=true)] private static extern IntPtr SetWindowLongPtr(IntPtr window, int index, IntPtr value);
    [DllImport("user32.dll", EntryPoint="GetWindowLongW", SetLastError=true)] private static extern int GetWindowLong32(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint="SetWindowLongW", SetLastError=true)] private static extern int SetWindowLong32(IntPtr window, int index, int value);
    [DllImport("kernel32.dll")] private static extern void SetLastError(uint error);
    public static IntPtr Handle(Window window) => new WindowInteropHelper(window).Handle;
    public static int[] Bounds(Window window)
    {
        if (!GetWindowRect(Handle(window), out var r)) throw new Win32Exception();
        return new[] { r.Left, r.Top, r.Right-r.Left, r.Bottom-r.Top };
    }
    public static object Geometry(Window window)
    {
        var r = Bounds(window); return new { x=r[0], y=r[1], width=r[2], height=r[3] };
    }
    internal static void Place(Window window, System.Drawing.Rectangle rectangle)
    {
        var handle = Handle(window);
        if (GetWindowRect(handle, out var current) && current.Left == rectangle.Left &&
            current.Top == rectangle.Top && current.Right == rectangle.Right && current.Bottom == rectangle.Bottom)
            return;
        const uint noActivateOrZOrder = 0x0014; // SWP_NOACTIVATE | SWP_NOZORDER
        if (!SetWindowPos(handle, IntPtr.Zero, rectangle.X, rectangle.Y,
                rectangle.Width, rectangle.Height, noActivateOrZOrder))
            throw new Win32Exception();
    }
    public static void Restore(Window window, JsonElement geometry, bool restoreSize=true)
    {
        if (geometry.ValueKind != JsonValueKind.Object) return;
        int x=(int)geometry.Number("x"), y=(int)geometry.Number("y"), width=(int)geometry.Number("width"), height=(int)geometry.Number("height");
        var work = System.Windows.Forms.Screen.FromRectangle(new System.Drawing.Rectangle(x,y,width,height)).WorkingArea;
        width=Math.Min(Math.Max(width,restoreSize ? 350 : 1),work.Width);
        height=Math.Min(restoreSize ? Math.Max(height,230) : Bounds(window)[3],work.Height);
        x=Math.Clamp(x,work.Left,work.Right-width); y=Math.Clamp(y,work.Top,work.Bottom-height);
        uint flags=restoreSize ? 0x0014u : 0x0015u;
        if (!SetWindowPos(Handle(window), IntPtr.Zero,x,y,width,height,flags)) throw new Win32Exception();
    }
    internal static void KeepInWorkingArea(Window window, System.Drawing.Rectangle work)
    {
        var bounds=Bounds(window);
        int x=Math.Clamp(bounds[0],work.Left,Math.Max(work.Left,work.Right-bounds[2]));
        int y=Math.Clamp(bounds[1],work.Top,Math.Max(work.Top,work.Bottom-bounds[3]));
        if(x==bounds[0] && y==bounds[1]) return;
        const uint moveOnly=0x0015; // SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        if(!SetWindowPos(Handle(window),IntPtr.Zero,x,y,0,0,moveOnly)) throw new Win32Exception();
    }
    public static long ExStyle(Window window) => IntPtr.Size == 8 ? GetWindowLongPtr(Handle(window),-20).ToInt64() : GetWindowLong32(Handle(window),-20);
    public static void Lock(Window window, bool locked)
    {
        long old=ExStyle(window), mask=0x20 | 0x08000000; // TRANSPARENT + NOACTIVATE, keep WPF LAYERED
        long value=locked ? old|mask : old&~mask;
        SetLastError(0);
        if (IntPtr.Size == 8) SetWindowLongPtr(Handle(window),-20,new IntPtr(value));
        else SetWindowLong32(Handle(window),-20,(int)value);
        int error=Marshal.GetLastWin32Error();
        if (error != 0) throw new Win32Exception(error);
        if (!SetWindowPos(Handle(window),new IntPtr(-1),0,0,0,0,0x0033)) throw new Win32Exception();
        if ((ExStyle(window)&mask) != (value&mask)) throw new Win32Exception("HUD 点击穿透标志设置失败");
    }
}
