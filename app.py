"""Small always-on-top local DPS window; no network service or game writes."""
import argparse
import json
import queue
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from collector import Collector, BattleChanged, DEFAULT_ADB, DEFAULT_SERIAL, ROOT
from metrics import observation_age
from battle_lifecycle import BattleRuntime


def number(value):
    return '—' if value is None else f'{value:,.1f}'


def timestamp():
    return datetime.now(timezone(timedelta(hours=8)))


class App:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.rescan = threading.Event()
        self.rescan.set()
        self.last = None
        self.collector = None
        self.last_frame_change = 0
        self.frame = None
        self.error = ''
        self.scanning = False
        self.battle_status = 'waiting'
        root.title('明日方舟 · 局内伤害')
        root.geometry(f'580x366+{max(0, root.winfo_screenwidth()-610)}+75')
        root.minsize(520, 310)
        root.attributes('-topmost', True)
        root.configure(bg='#182127')
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#182127', foreground='#edf2f4')
        style.configure('TButton', padding=(10, 6), background='#33434d', foreground='#edf2f4', borderwidth=0)
        style.map('TButton', background=[('active', '#485e6b'), ('disabled', '#26343d')])
        style.configure('TCheckbutton', background='#182127', foreground='#edf2f4')
        style.map('TCheckbutton', background=[('active', '#26343d')])
        style.configure('Treeview', background='#182127', fieldbackground='#182127', foreground='#edf2f4',
                        rowheight=34, borderwidth=0, font=('Microsoft YaHei UI', 10))
        style.configure('Treeview.Heading', background='#26343d', foreground='#b9d2de', padding=(6, 7),
                        font=('Microsoft YaHei UI', 9))
        style.map('Treeview', background=[('selected', '#305569')])
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        top = ttk.Frame(root, padding=(16, 10, 16, 5))
        top.grid(row=0, column=0, sticky='ew')
        top.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value='准备连接…')
        self.status_label = ttk.Label(top, textvariable=self.status, font=('Microsoft YaHei UI', 11, 'bold'))
        self.status_label.grid(row=0, column=0, sticky='w')
        self.pin = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='置顶', variable=self.pin,
            command=lambda: root.attributes('-topmost', self.pin.get())).grid(row=0, column=1)
        self.clock = tk.StringVar(value='等待本局数据')
        ttk.Label(root, textvariable=self.clock, foreground='#b9d2de', padding=(16, 0, 16, 10)).grid(row=1, column=0, sticky='w')
        table_frame = ttk.Frame(root, padding=(12, 0))
        table_frame.grid(row=2, column=0, sticky='nsew')
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=('name','total','dps','recent'), show='headings', selectmode='browse', height=5)
        for key, title, width, anchor in (
            ('name','干员',140,'w'), ('total','总伤害',125,'e'),
            ('dps','全局 DPS',112,'e'), ('recent','近 5 秒 DPS ≈',130,'e')):
            self.table.heading(key, text=title, anchor=anchor)
            self.table.column(key, width=width, minwidth=85, anchor=anchor, stretch=True)
        self.table.grid(row=0, column=0, sticky='nsew')
        bar = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=bar.set)
        self.table.bind('<<TreeviewSelect>>', self.select)
        self.detail = tk.StringVar(value='等待可靠采样；近 5 秒指标从开始观察时计时。')
        ttk.Label(root, textvariable=self.detail, foreground='#b9d2de', wraplength=530,
                  padding=(16,8,16,4), font=('Microsoft YaHei UI',9)).grid(row=3,column=0,sticky='ew')
        self.summary = tk.StringVar(value='合计 —')
        ttk.Label(root,textvariable=self.summary,padding=(16,2,16,4)).grid(row=4,column=0,sticky='w')
        bottom=ttk.Frame(root,padding=(16,6,16,12))
        bottom.grid(row=5,column=0,sticky='ew')
        bottom.columnconfigure(0,weight=1)
        ttk.Label(bottom,text='含过量伤害 · 按游戏时间',foreground='#b9d2de',font=('Microsoft YaHei UI',9)).grid(row=0,column=0,sticky='w')
        self.scan_button=ttk.Button(bottom,text='重新定位',command=self.rescan.set)
        self.scan_button.grid(row=0,column=1,padx=(0,8))
        self.export_button=ttk.Button(bottom,text='导出本局',command=self.export,state='disabled')
        self.export_button.grid(row=0,column=2)
        self.worker=threading.Thread(target=self.collect,daemon=True)
        self.worker.start()
        root.after(100,self.tick)

    def collect(self):
        try:
            self._collect()
        except Exception as exc:
            traceback.print_exc()
            self.events.put(('error',str(exc)))
        finally:
            if self.collector:
                self.collector.close()

    def _collect(self):
        runtime = BattleRuntime()
        first_connection = True
        last_written_key = None
        last_written_at = 0
        capture_dir=ROOT/'runtime'/'captures'
        capture_dir.mkdir(parents=True,exist_ok=True)
        capture_path=capture_dir/f'{timestamp():%Y%m%d-%H%M%S}.jsonl'
        with capture_path.open('a',encoding='utf-8') as out:
            while not self.stop.is_set():
                if self.rescan.is_set():
                    self.rescan.clear()
                    self.events.put(('scanning',True))
                    if self.collector:
                        runtime.disconnected('relocating')
                        self.collector.close()
                    self.collector=Collector(self.args.adb,self.args.serial,log=lambda x:print(x,flush=True))
                    try:
                        force = not first_connection
                        first_connection = False
                        self.collector.connect(force=force, lifecycle=True)
                    except Exception as exc:
                        traceback.print_exc()
                        self.events.put(('error',str(exc)))
                        runtime.disconnected(str(exc))
                        self.collector.close()
                        self.collector=None
                    self.events.put(('scanning',False))
                if self.collector:
                    try:
                        observation = runtime.poll(self.collector)
                        self.events.put(('lifecycle',runtime.sessions.state))
                        sample = runtime.display_sample()
                        if sample and observation.kind in ('sample', 'end'):
                            key=(sample['session'],sample['frame'],sample['sum_damage'],
                                 runtime.sessions.state)
                            if key != last_written_key or time.monotonic()-last_written_at >= 5:
                                out.write(json.dumps(sample,ensure_ascii=False)+'\n')
                                out.flush()
                                last_written_key=key
                                last_written_at=time.monotonic()
                            self.events.put(('sample',sample))
                    except BattleChanged as exc:
                        runtime.disconnected(str(exc))
                        self.events.put(('error',str(exc)))
                        self.rescan.set()
                    except Exception as exc:
                        self.events.put(('error',str(exc)))
                for archive in runtime.sessions.pop_archives():
                    out.write(json.dumps({'archive_ready':archive},ensure_ascii=False)+'\n')
                    out.flush()
                self.stop.wait(0.2)
            runtime.disconnected('capture_stopped')
            out.write(json.dumps({'lifecycle_stopped':runtime.sessions.live()},ensure_ascii=False)+'\n')
            out.flush()

    def tick(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='sample':
                    self.error=''
                    self.last=value
                    if (value['session'],value['frame'])!=self.frame:
                        self.frame=(value['session'],value['frame'])
                        self.last_frame_change=value['captured_monotonic']
                    self.render(value)
                elif kind=='error':
                    self.error=value
                elif kind=='lifecycle':
                    self.battle_status=value
                    self.error=''
                elif kind=='scanning':
                    self.scanning=value
                    self.scan_button.configure(state='disabled' if value else 'normal')
                    if value:
                        self.error=''
        except queue.Empty:
            pass
        age=observation_age(self.last)
        if self.scanning:
            label='正在定位 · 保留上次记录' if self.last else '正在定位本局…'
        elif self.error:
            label='读取中断 · 显示最后有效记录' if self.last else '等待战斗数据'
            self.detail.set(self.error)
        elif not self.last:
            label='等待战斗数据'
        elif self.battle_status=='ended':
            label='战斗结束'
        elif self.battle_status=='frozen':
            label='战局冻结 · 保留最后有效记录'
        elif age>2:
            label='数据已过期 · 请重新定位'
        elif time.monotonic()-self.last_frame_change>0.8:
            label='暂停 / 等待战斗推进'
        else:
            label='正在统计'
        self.status.set(label)
        self.status_label.configure(foreground='#f1bc82' if self.error or (self.last and age>2 and not self.scanning) else '#edf2f4')
        self.root.after(100,self.tick)

    def render(self,sample):
        seconds=sample['game_time']
        self.clock.set(f'游戏时间 {int(seconds)//60:02}:{seconds%60:04.1f}    ·    {len(sample["rows"])} 个统计条目')
        live_ids=set()
        for idx,row in enumerate(sample['rows']):
            key=row['id'];live_ids.add(key)
            values=(row['name'],number(row['damage']),number(row['dps']),number(row['recent_dps']))
            if self.table.exists(key):
                if tuple(map(str,self.table.item(key,'values')))!=tuple(map(str,values)):
                    self.table.item(key,values=values)
                self.table.move(key,'',idx)
            else:
                self.table.insert('',idx,iid=key,values=values)
        for key in self.table.get_children():
            if key not in live_ids:self.table.delete(key)
        total_label = '干员合计' if sample.get('statistics_complete', True) else '已知累计（数据不全）'
        self.summary.set(f'{total_label} {number(sample["sum_damage"])}    ·    全局 DPS {number(sample["sum_damage"]/seconds if seconds>0 else None)}')
        self.export_button.configure(state='normal')
        self.select()

    def select(self,_event=None):
        if not self.last:return
        chosen=self.table.selection()
        row=next((r for r in self.last['rows'] if chosen and r['id']==chosen[0]),None)
        if row:
            share = f'{row["share"]:.1%}' if row['share'] is not None else '—（全队数据不全）'
            self.detail.set(f'{row["name"]} · 占比 {share} · 物理 {number(row["physical"])} · 法术 {number(row["arts"])} · 真实 {number(row["true"])} · 元素伤害 {number(row["elemental"])}')
        else:
            self.detail.set('选中干员查看伤害分类。近 5 秒 DPS 为采样估算；“—”表示观察不足。')

    def export(self):
        if not self.last:return
        path=filedialog.asksaveasfilename(parent=self.root,title='导出当前战斗记录',
            initialdir=str(ROOT/'runtime'),initialfile=f'战斗统计-{timestamp():%Y%m%d-%H%M%S}.json',
            defaultextension='.json',filetypes=[('JSON 战斗记录','*.json')])
        if path:
            payload=dict(self.last,exported_at=timestamp().isoformat(),
                data_age_seconds=round(observation_age(self.last),2),
                capture_status='unavailable' if self.error else 'locating' if self.scanning else 'current' if observation_age(self.last)<2 else 'stale')
            try:
                Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('导出失败',str(exc),parent=self.root)

    def close(self):
        self.stop.set()
        self.root.withdraw()
        self.finish_close()

    def finish_close(self):
        if self.worker.is_alive():
            self.root.after(100,self.finish_close)
        else:
            self.root.destroy()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--adb',default=DEFAULT_ADB)
    parser.add_argument('--serial',default=DEFAULT_SERIAL)
    parser.add_argument('--capture-seconds',type=float,default=0)
    args=parser.parse_args()
    (ROOT/'runtime').mkdir(exist_ok=True)
    # One owner for the device channel. A second launch cannot tear it down.
    instance_lock=socket.socket()
    try:
        instance_lock.bind(('127.0.0.1',27380))
        instance_lock.listen(1)
    except OSError:
        instance_lock.close()
        if args.capture_seconds:
            raise SystemExit('统计窗已在运行，请先关闭后进行命令行采集')
        root=tk.Tk();root.withdraw()
        messagebox.showinfo('统计窗已运行','请使用已经打开的“明日方舟 · 局内伤害”窗口。',parent=root)
        root.destroy()
        return
    if args.capture_seconds:
        from capture_battles import capture
        dest=ROOT/'runtime'/f'battles-{timestamp():%Y%m%d-%H%M%S}'
        capture(args.capture_seconds, dest, args.adb, args.serial)
        print(dest)
        return
    log=(ROOT/'runtime'/f'app-{timestamp():%Y%m%d-%H%M%S}.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=sys.stderr=log
    root=tk.Tk()
    App(root,args)
    root.mainloop()


if __name__=='__main__':
    main()
