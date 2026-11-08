"""Check MaaCore connectivity without queuing a game task."""

import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('maa_root', type=Path)
    parser.add_argument('adb', type=Path)
    parser.add_argument('serial')
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--native-adb', action='store_true',
                        help='Use the legacy subprocess backend for comparison')
    args = parser.parse_args()

    maa_root = args.maa_root.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(maa_root / 'Python'))
    from asst.asst import Asst
    from asst.utils import InstanceOptionType

    result = {'serial': args.serial, 'adb_lite': not args.native_adb,
              'connected': False, 'tasks_submitted': 0}
    with (output / 'callbacks.jsonl').open('x', encoding='utf-8') as callbacks:
        @Asst.CallBackType
        def callback(message, detail, _):
            event = {'message': message, 'detail': json.loads(detail.decode('utf-8'))}
            callbacks.write(json.dumps(event, ensure_ascii=False) + '\n')
            callbacks.flush()

        result['loaded'] = Asst.load(maa_root, user_dir=output)
        if result['loaded']:
            instance = Asst(callback)
            try:
                result['version'] = instance.get_version()
                result['keep_adb_server'] = instance.set_instance_option(
                    InstanceOptionType.kill_on_adb_exit, '0')
                result['backend_configured'] = instance.set_instance_option(
                    InstanceOptionType.adblite_enabled, '0' if args.native_adb else '1')
                if result['keep_adb_server'] and result['backend_configured']:
                    result['connected'] = instance.connect(
                        str(args.adb.resolve()), args.serial, 'General')
            finally:
                del instance

    summary = json.dumps(result, ensure_ascii=False)
    (output / 'result.json').write_text(summary + '\n', encoding='utf-8')
    print(summary)
    return 0 if result['connected'] else 1


if __name__ == '__main__':
    sys.exit(main())
