#!/usr/bin/env python3
"""Mac R4 0.4.0 delivery and reviewed system setup; no stored credentials."""
import argparse
import base64
import datetime
import ipaddress
import json
import uuid
import hashlib
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).absolute().parents[1]
VERSION='0.4.0'
PREFIX='junshu-camera-pi-'+VERSION
SSH_FLAGS=['-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes']


def validate_target(target):
    if not isinstance(target,str) or not re.fullmatch(r'junshu@[A-Za-z0-9][A-Za-z0-9.-]{0,240}',target):
        raise ValueError('SSH 目标无效：仅支持 junshu@主机名 或 junshu@IPv4，不含空格、选项或命令')
    host=target.split('@',1)[1]
    if re.fullmatch(r'[0-9.]+',host):
        try:ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:raise ValueError('SSH IPv4 无效') from None
    elif any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in host.split('.')):
        raise ValueError('SSH 主机名无效')
    return target


def target_input():
    target=validate_target(input('SSH 目标（回车使用 junshu@camera-test.local）：').strip() or 'junshu@camera-test.local')
    print('目标：'+target)
    print('SSH/sudo密码由终端私下输入，不保存密码。强制核对已有可信主机记录。')
    return target


def ssh(target,command,*,data=None,capture=False,terminal=False):
    validate_target(target)
    args=['ssh',*(['-t'] if terminal else []),*SSH_FLAGS,'--',target,command]
    if terminal and capture:
        # Inherit user's real terminal input. Forward sudo prompt immediately;
        # remote PTY disables password echo, and credentials never enter Python.
        process=subprocess.Popen(args,stdin=None,stdout=subprocess.PIPE,stderr=None,text=True)
        retained=[];size=0
        while True:
            char=process.stdout.read(1)
            if not char:break
            print(char,end='',flush=True)
            size+=len(char)
            if size<=1048576:retained.append(char)
        status=process.wait()
        result=subprocess.CompletedProcess(args,status,''.join(retained),'')
        if size>1048576:raise ValueError('SSH report output limit; inspect Terminal output')
        return result
    return subprocess.run(args,input=data,capture_output=capture,text=True,check=True)



def checked_archive():
    source=ROOT/'pi-r4-next/check_package.py'
    spec=importlib.util.spec_from_file_location('share_package_checker',source)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    archive=ROOT/'dist-next'/(PREFIX+'.tar.gz')
    checksum=archive.with_name(archive.name+'.sha256')
    for path in (archive,checksum):
        checker.regular_file(path.parent,path.name)
    line=checksum.read_text(encoding='ascii')
    if not re.fullmatch('[0-9a-f]{64}  '+re.escape(archive.name)+'\n',line):
        raise ValueError('Invalid archive checksum sidecar')
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=line[:64]:
        raise ValueError('Archive SHA256 mismatch')
    contents=checker.read_archive(archive)
    digest=hashlib.sha256(contents['MANIFEST.sha256']).hexdigest()
    return archive,checksum,digest,source.read_text().rsplit("\nif __name__ == '__main__':",1)[0]


def import_release(target):
    validate_target(target)
    print('导入 R4 0.4.0 整合版，校验独立 ~/{}；相同内容可重试，拒绝未知内容。'.format(PREFIX))
    print('请先在相机触屏退出 Share；会备份并更新同一个 Share 图标。旧包、Power、照片和设置保留。')
    print('不启动GUI，不安装服务/依赖，不修改Wi-Fi、桌面自启动或登录。')
    if input('确认向此SSH目标导入？输入 yes：')!='yes':
        print('Import canceled / 已取消；没有连接设备。');return
    archive,checksum,digest,checker=checked_archive()
    print('包：'+str(archive)+'（'+str(archive.stat().st_size)+' bytes）')
    prepare='''set -eu
[ "$(id -un)" = junshu ] && [ "$HOME" = /home/junshu ]
parent="$HOME/.local/state/junshu-camera/imports"
mkdir -p -- "$parent"
incoming=$(mktemp -d "$parent/r4-next.XXXXXXXX")
basename -- "$incoming"
'''
    name=ssh(target,'sh -s',data=prepare,capture=True).stdout.strip()
    if not re.fullmatch(r'r4-next\.[A-Za-z0-9]{8}',name):
        raise ValueError('Unexpected receiver directory response')
    relative='.local/state/junshu-camera/imports/'+name
    subprocess.run(['scp',*SSH_FLAGS,'--',str(archive),str(checksum),target+':'+relative+'/'],check=True)
    expected_archive=hashlib.sha256(archive.read_bytes()).hexdigest()
    # Execute only trusted Mac checker; archive contents are data until all SHA checks pass.
    remote=checker+'\n'+'''
import os
if Path.home()!=Path('/home/junshu') or os.geteuid()==0:
    raise SystemExit('Use junshu without sudo')
incoming=Path.home()/'.local/state/junshu-camera/imports'/sys.argv[1]
archive=incoming/(PREFIX+'.tar.gz')
receive_archive(archive,incoming/(PREFIX+'.tar.gz.sha256'),sys.argv[2],Path.home())
print('Import verified; receiver archive retained at '+str(incoming))
'''
    ssh(target,"/usr/bin/python3 -I -B - '"+name+"' '"+expected_archive+"'",data=remote)
    launcher=(ROOT/'tools/create_pi_next_launcher.py').read_text()
    ssh(target,"/usr/bin/python3 -I -B - --expected-manifest-sha256 '"+digest+"'",data=launcher)
    print('整合版导入完成。请在Pi触屏重新双击同一个 Junshu Camera R4 Share 图标，手动核对预览、拍照和图库。')
    print('导入未启用白灯或开机启动；请先手动测试同一个 Share 图标。')
    print('沿用现有 pwr.1 开关机服务；没有安装、替换、启停任何服务。')

REPORTS=ROOT/'qa/r4-integrated-20261026/device-reports'


def save_report(action,result,*,report_dir=None):
    """Retain bounded JSON only; SSH passwords/terminal prompts are never written."""
    folder=Path(report_dir) if report_dir is not None else REPORTS
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+action+'-'+uuid.uuid4().hex+'.json')
    payload={'action':action,'result':result,'time':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    text=json.dumps(payload,ensure_ascii=False,indent=2)
    if len(text.encode())>1048576:raise ValueError('report output limit')
    with path.open('x',encoding='utf-8') as output:output.write(text+'\n')
    path.chmod(0o600)
    print('报告保留：'+str(path))
    return path


def parse_result(text):
    if len(text)>1048576:raise ValueError('report output limit')
    # sudo PTY prompt can precede the JSON. Decode only an actual JSON object.
    decoder=json.JSONDecoder()
    for index,char in enumerate(text):
        if char!='{':continue
        try:result,end=decoder.raw_decode(text,index)
        except ValueError:continue
        if isinstance(result,dict):return result
    raise ValueError('No complete system JSON report; inspect Terminal output, no configuration inferred')


def system_action(target,action,*,expected_manifest=None,reviewed=False,report_dir=None):
    validate_target(target)
    allowed=('probe','verify','enable-led','disable-led','enable-autostart','disable-autostart')
    if action not in allowed:raise ValueError('unsupported system action')
    if action=='enable-led' and not reviewed:raise ValueError('Actual C/D GPIO22 report review required')
    args=action
    if expected_manifest is not None:
        if not re.fullmatch('[0-9a-f]{64}',expected_manifest):raise ValueError('invalid trusted manifest')
        args+=' --expected-manifest-sha256 '+expected_manifest
    if reviewed:args+=' --gpio-reviewed'
    source=(ROOT/'tools/pi_next_system.py').read_text()
    if action in ('enable-led','disable-led'):
        encoded=base64.b64encode(source.encode()).decode('ascii')
        bootstrap='import base64;exec(compile(base64.b64decode("'+encoded+'"),"<trusted-mac-system>","exec"))'
        result=ssh(target,"sudo /usr/bin/python3 -I -B -c '"+bootstrap+"' "+args,capture=True,terminal=True)
    else:
        try:result=ssh(target,'/usr/bin/python3 -I -B - '+args,data=source,capture=True)
        except subprocess.CalledProcessError as error:
            if error.stdout:
                report=parse_result(error.stdout);save_report(action,report,report_dir=report_dir);print(json.dumps(report,ensure_ascii=False,indent=2))
            raise
    report=parse_result(result.stdout)
    save_report(action,report,report_dir=report_dir)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if result.returncode:raise ValueError('System action refused; retained report contains evidence')
    return report


def enable_led(target):
    print('先只读核查白灯配置/驱动状态；不会热切换 GPIO 或自动重启。')
    report=system_action(target,'verify')
    lifecycle=report.get('led_lifecycle',{})
    if lifecycle.get('enable_noop') is True and lifecycle.get('state') in ('staged','running_managed'):
        state=lifecycle['state']
        print('白灯已配置：'+('已验证内核驱动逻辑状态与所有权；本次只读返回，实物亮度/接线仍待验收。' if state=='running_managed' else '已暂存、尚未确认运行；本次只读返回，不重新接线或配置。'))
        return {'changed':False,'configured':True,'reboot_required':False,'live_changed':False,
                'pending_boot':state=='staged','led_state':state}
    if not report.get('led_ready') or lifecycle.get('state','free_candidate')!='free_candidate':
        raise ValueError('本次 GPIO22 核查未通过；停止白灯配置')
    print('GPIO22/物理15仍是候选，实际 C/D 复核前不接线。')
    if input('仅当 C/D 已复核实际报告并批准 GPIO22 候选后，输入 C/D已复核GPIO22：')!='C/D已复核GPIO22':
        print('取消白灯配置；没有申请 GPIO 或修改配置。');return
    system_action(target,'enable-led',reviewed=True)
    print('仅为下次开机暂存配置；未操作实时 GPIO。接线前正常关机并断开全部电源，按 D 指引操作。')


LOGS_SOURCE=r'''
import datetime
import json
from pathlib import Path
import re
import subprocess

# Match only exact Task2 producer messages and the standard logger prefix.
# Numeric limits are report sanity bounds, never hardware acceptance targets.
number=r'[0-9]{1,6}\.[0-9]'
stamp=r'([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3})'
patterns={
    'camera':re.compile(r'(?:'+stamp+r' INFO camera_r4\.camera )?preview camera delivered_fps=('+number+r') exposure_us=([1-9][0-9]{0,8})'),
    'ui':re.compile(r'(?:'+stamp+r' INFO camera_r4\.gui )?preview UI prepared_fps=('+number+r') processing_ms=('+number+r') frame_age_ms=('+number+r')')}

def performance_sample(line,kind):
    if len(line)>256:return None
    match=patterns[kind].fullmatch(line)
    if not match:return None
    values=match.groups();timestamp=None
    if values[0] is not None:
        try:timestamp=datetime.datetime.strptime(values[0],'%Y-%m-%d %H:%M:%S,%f').isoformat(timespec='milliseconds')
        except ValueError:return None
    fps=float(values[1])
    if not 0<=fps<=240:return None
    if kind=='camera':
        exposure=int(values[2])
        if not 1<=exposure<=120000000:return None
        return {'delivered_fps':fps,'exposure_us':exposure,'timestamp_local':timestamp}
    processing,age=float(values[2]),float(values[3])
    if not (0<=processing<=60000 and 0<=age<=60000):return None
    return {'prepared_fps':fps,'processing_ms':processing,'frame_age_ms':age,'timestamp_local':timestamp}

home=Path.home()
report={'logs':{},'performance':{'camera':[],'ui':[]}}
for name in ('camera-r4-next.log','autostart-r4.log','camera-r4-power.log'):
    path=home/'.local/state/junshu-camera'/name
    item={'exists':path.is_file() and not path.is_symlink()}
    if item['exists']:
        try:
            item.update(bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns)
            # Fixed markers only. Never copy arbitrary log lines containing photo names/tokens.
            with path.open('rb') as stream:
                start=max(0,item['bytes']-65536)
                stream.seek(start);tail=stream.read(65536).decode('utf-8','replace')
                if start:tail=tail.partition('\n')[2]  # Never parse a cut line.
            if name=='camera-r4-next.log':
                for line in tail.splitlines():
                    for kind in ('camera','ui'):
                        sample=performance_sample(line,kind)
                        if sample is not None:
                            report['performance'][kind]=(report['performance'][kind]+[sample])[-20:]
            item['markers']=[marker for marker in ('Junshu Camera R4 Integrated 0.4.0','startup failed','Graphical session missing','Qt platform=','preparing','committed','uncertain') if marker in tail]
        except OSError as error:item['error']=type(error).__name__
    report['logs'][name]=item
try:
    result=subprocess.run(['/usr/bin/systemctl','show','junshu-camera-power.service','--property=ActiveState,SubState,UnitFileState'],capture_output=True,text=True,timeout=5)
    report['power']={'returncode':result.returncode,'stdout':result.stdout[:4096]}
except (OSError,subprocess.SubprocessError) as error:report['power']={'error':type(error).__name__}
print(json.dumps(report,ensure_ascii=False,indent=2))
'''


def logs(target):
    errors=[]
    # Each source is independent: missing GUI output cannot hide Power/system status.
    try:system_action(target,'verify')
    except (OSError,ValueError,subprocess.SubprocessError) as error:errors.append(str(error));print('系统核查未通过：'+str(error))
    try:
        result=ssh(target,'/usr/bin/python3 -I -B -',data=LOGS_SOURCE,capture=True)
        report=parse_result(result.stdout);save_report('logs',report);print(json.dumps(report,ensure_ascii=False,indent=2))
    except (OSError,ValueError,subprocess.SubprocessError) as error:errors.append(str(error))
    if errors:raise ValueError('部分只读报告未取得；其余结果已保留：'+'; '.join(errors))


def rollback_share2(target):
    """Same-icon rollback only to the retained locally verified Share2 archive."""
    source=ROOT/'pi-r4-transfer-share2/check_package.py'
    spec=importlib.util.spec_from_file_location('share2_rollback_check',source)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    archive=ROOT/'dist-transfer/junshu-camera-pi-0.3.0-share.2.tar.gz'
    side=archive.with_name(archive.name+'.sha256')
    checker.regular_file(archive.parent,archive.name);checker.regular_file(side.parent,side.name)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    if side.read_text()!=digest+'  '+archive.name+'\n':raise ValueError('retained Share2 archive checksum mismatch')
    contents=checker.read_archive(archive)
    manifest=hashlib.sha256(contents['MANIFEST.sha256']).hexdigest()
    print('先停用新版开机启动（如曾启用）并从触屏退出；回退只更新原 Share 桌面/菜单入口，不删除包或修改 Power。')
    launcher=(ROOT/'tools/create_pi_next_launcher.py').read_text()
    ssh(target,"/usr/bin/python3 -I -B - --version 0.3.0-share.2 --expected-manifest-sha256 '"+manifest+"'",data=launcher)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('import','probe','verify','logs','enable-autostart','disable-autostart','enable-led','disable-led','rollback-share2'))
    args=parser.parse_args(argv);target=target_input()
    if args.action=='import':import_release(target)
    elif args.action=='logs':logs(target)
    elif args.action=='enable-led':enable_led(target)
    elif args.action=='rollback-share2':rollback_share2(target)
    elif args.action=='enable-autostart':
        _,_,digest,_=checked_archive();system_action(target,args.action,expected_manifest=digest)
    else:system_action(target,args.action)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,subprocess.SubprocessError,EOFError,KeyboardInterrupt) as error:
        sys.exit('R4 整合工具已停止；本次文件均保留：'+str(error))
