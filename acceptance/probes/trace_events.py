"""Explicitly attached research observer, not the product collector.

Requires Frida 17.26.1 and a matching server already running on the test device.
No server installation, game launch, or ADB configuration is performed here.
"""
import argparse
import json
from pathlib import Path
import sys
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--remote', default='127.0.0.1:27043')
    parser.add_argument('--seconds', type=float, default=0,
                        help='0: wait for Enter; positive: bounded capture duration')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error('--seconds must be nonnegative')
    import frida
    if frida.__version__ != '17.26.1':
        parser.error('this research probe was validated with Frida 17.26.1')

    source = Path(__file__).with_name('shield_events_android2791.js').read_text(encoding='utf-8')
    ready, failed = threading.Event(), threading.Event()
    # Refuse to overwrite an earlier experiment.
    with args.output.open('x', encoding='utf-8') as out:
        device = frida.get_device_manager().add_remote_device(args.remote)
        session = device.attach(args.pid, realm='emulated')
        script = session.create_script(source)

        def message(item, _data):
            out.write(json.dumps(item, ensure_ascii=False) + '\n')
            out.flush()
            if item.get('type') == 'error':
                failed.set()
                ready.set()
                print(json.dumps(item), file=sys.stderr, flush=True)
            elif item.get('payload', {}).get('ready'):
                ready.set()
                print('READY: event observer attached; start MAA now.', flush=True)

        script.on('message', message)
        try:
            script.load()
            if not ready.wait(20) or failed.is_set():
                raise RuntimeError('probe did not initialize; do not start experiment')
            if args.seconds:
                failed.wait(args.seconds)
            else:
                input('Press Enter after pausing the game to detach.\n')
            script.exports_sync.stop()
            if failed.is_set():
                raise RuntimeError('capture contains probe errors; evidence is incomplete')
        finally:
            try:
                script.unload()
            finally:
                session.detach()


if __name__ == '__main__':
    main()
