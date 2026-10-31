"""Read-only preflight and exclusive BCM17 input adapter, for Pi 3B+ only."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess


def command(args):
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=4)
        return {'command':args,'code':result.returncode,'stdout':result.stdout.strip(),'stderr':result.stderr.strip()}
    except (OSError,subprocess.TimeoutExpired) as error:
        return {'command':args,'code':-1,'stdout':'','stderr':str(error)}


def pin_is_input(text):
    return bool(re.search(r'^\s*17:\s+ip\b',text,re.M) or
                re.search(r'^GPIO\s+17:.*\bfsel=0\s+func=INPUT\b',text,re.M))


def inspect_chips(module,paths):
    result=[]
    for path in paths:
        chip=None
        try:
            chip=module.Chip(str(path));info=chip.get_info()
            if info.num_lines<=17:continue
            identity=os.fstat(chip.fd)
            if not stat.S_ISCHR(identity.st_mode):raise RuntimeError('GPIO控制器不是字符设备')
            line=chip.get_line_info(17)
            result.append({'path':str(path),'realpath':str(Path(path).resolve()),
                'device_major':os.major(identity.st_rdev),'device_minor':os.minor(identity.st_rdev),
                'chip_name':info.name,'label':info.label,'name':line.name,'used':line.used,
                'consumer':line.consumer,'direction':line.direction.name,
                'mapping_ok':info.label=='pinctrl-bcm2835' and line.name=='GPIO17'})
        except Exception as error:result.append({'path':str(path),'error':str(error),'mapping_ok':False})
        finally:
            if chip is not None:chip.close()
    return result


def select_chip(rows):
    if any(row.get('error') for row in rows):
        raise RuntimeError('部分GPIO设备身份或状态未能核查，不能排除歧义')
    candidates=[row for row in rows if row.get('mapping_ok')]
    identities={(row['device_major'],row['device_minor']) for row in candidates}
    if len(identities)!=1:
        raise RuntimeError('无法唯一确认GPIO17对应的pinctrl-bcm2835控制器（按真实设备号核对）')
    candidates=[row for row in rows if (row['device_major'],row['device_minor']) in identities]
    if any(not row['mapping_ok'] for row in candidates):
        raise RuntimeError('同一GPIO设备的别名映射不一致，不能确认GPIO17')
    # Identical labels alone are insufficient. Aliases must resolve to the
    # same opened character device AND report consistent kernel line state.
    observations={(row['chip_name'],row['label'],row['name'],row['used'],row['consumer'],row['direction'])
                  for row in candidates}
    if len(observations)!=1:raise RuntimeError('GPIO别名状态不一致或核查期间有变化，请停止其他使用者后重查')
    chosen=dict(candidates[0])
    chosen['aliases']=[row['path'] for row in candidates]
    chosen['path']=chosen['realpath']
    if chosen['used']:raise RuntimeError('GPIO17已被占用：'+str(chosen['consumer']))
    if chosen['direction']!='INPUT':raise RuntimeError('GPIO17当前不是输入，需先核对用途')
    return chosen


def inspect_gpio(details=True):
    report={'probe_version':'0.3.0-l1.2','bcm':17,'physical_signal':11,'physical_gnd':9,'read_only':True,'errors':[],
            'system':platform.platform(),'python':platform.python_version(),'uid':os.getuid()}
    errors=report['errors']
    try:report['model']=Path('/proc/device-tree/model').read_text().rstrip('\0')
    except OSError:report['model']='unknown'
    if not report['model'].startswith('Raspberry Pi 3 Model B Plus'):errors.append('未确认Pi 3B+型号，停止L1接入')
    try:
        module=importlib.import_module('gpiod')
        report['gpiod']=getattr(module,'__version__','unknown')
        if not hasattr(module,'LineSettings') or not hasattr(module,'request_lines'):
            raise RuntimeError('需要系统python3的libgpiod 2.x接口，当前接口不兼容')
        report['chips']=inspect_chips(module,sorted(Path('/dev').glob('gpiochip*')))
        chosen=select_chip(report['chips']);report['chip']=chosen['path']
        report['selected_chip']=chosen
        if not os.access(chosen['path'],os.R_OK|os.W_OK):errors.append('当前用户没有GPIO设备读写权限')
    except Exception as error:errors.append('GPIO库/占用检查失败：'+str(error))
    report['pinmux']=[]
    for tool in ('pinctrl','raspi-gpio'):
        executable=shutil.which(tool)
        if executable:
            result=command([executable,'get','17']);report['pinmux'].append(result)
            if result['code']==0:
                if not pin_is_input(result['stdout']):errors.append('GPIO17复用/方向不是已确认的普通输入')
                break
    else:errors.append('无法读取GPIO17实际复用状态；需要pinctrl get 17或raspi-gpio get 17的成功结果')
    if details:
        report['groups']=command(['id'])
        report['os_release']=command(['cat','/etc/os-release'])
        report['libraries']={name:bool(importlib.util.find_spec(name)) for name in ['gpiod','gpiozero','lgpio']}
        report['boot_gpio_config']={}
        for filename in ('/boot/firmware/config.txt','/boot/config.txt'):
            try:
                report['boot_gpio_config'][filename]=[line for line in Path(filename).read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#') and
                    re.search(r'gpio|dtoverlay|dtparam|include|^\[',line,re.I)]
            except OSError:pass
        report['active_services']=command(['systemctl','list-units','--type=service','--state=running','--no-pager','--no-legend'])
        report['process_names']=command(['ps','-eo','pid,comm'])
        report['installed_versions']=[p.name for p in sorted(Path.home().glob('junshu-camera-pi-*')) if p.is_dir()]
        report['manual_review']='结合配置与服务核对GPIO17用途。内核占用/当前pinmux检查不能排除绕过内核直接访问GPIO的进程。此报告不授权带电接线。'
    report['runtime_check_passed']=not errors
    return report


class GPIO17Input:
    def __init__(self,module=None,probe=None):
        report=(probe or (lambda:inspect_gpio(details=False)))()
        if report['errors']:raise RuntimeError('；'.join(report['errors']))
        self.module=module or importlib.import_module('gpiod')
        # request_lines is exclusive: another consumer winning the race fails
        # here. Never force-release, set output values, or fall back to raw MMIO.
        self.request=self.module.request_lines(report['chip'],consumer='junshu-camera-L1',config={
            17:self.module.LineSettings(direction=self.module.line.Direction.INPUT,
                                       bias=self.module.line.Bias.PULL_UP,active_low=True)})
    def read(self):
        value=self.request.get_value(17)
        if value not in (self.module.line.Value.ACTIVE,self.module.line.Value.INACTIVE):
            raise RuntimeError('GPIO17返回未知电平')
        return value==self.module.line.Value.ACTIVE
    def close(self):
        if self.request is not None:
            self.request.release();self.request=None


def main():
    report=inspect_gpio()
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['runtime_check_passed'] else 2


if __name__=='__main__':raise SystemExit(main())
