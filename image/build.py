#!/usr/bin/env python3
"""Build a regular-file ZERO 3W system image on macOS or Linux; never opens a disk device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from motion_runtime import ARCHIVE_SHA, BUILD, LEASE_SHA, MARKER, read_runtime

HERE = Path(__file__).resolve().parent
BOARD = HERE.parent / 'robot/base'
BRIDGE = HERE.parent / 'robot/bridge'
DEFAULT = HERE.parent / 'output/zero3w-base'
KERNEL = '6.1.84-10-rk2410-nocsf'
SOURCES = {
    'radxa.img.xz': ('https://github.com/radxa-build/radxa-zero3/releases/download/rsdk-b1/radxa-zero3_bookworm_kde_b1.output_512.img.xz', '47b70ec81377f618505951fc9b4c40784e983c7fdff9706fdf79c13b6cd20730'),
    'tof.whl': ('https://files.pythonhosted.org/packages/1a/7a/81c571da531b39f6d1669a85b34c06c8073c87bbabb92814affd154f7efc/VL53L5CX-1.0.1-py3-none-any.whl', '8e11d48470836a4c27c243ae2ba58c0f3ee67ecf2985c0456133eef0cb151dbb'),
    'smbus.whl': ('https://files.pythonhosted.org/packages/85/9f/2235ba9001e3c29fc342eeb222104420bcb7bac51555f0c034376a744075/smbus2-0.5.0-py2.py3-none-any.whl', '1a15c3b9fa69357beb038cc0b5d37939702f8bfde1ddc89ca9f17d8461dbe949'),
    'audio.dts': ('https://raw.githubusercontent.com/kissqy/Microduck-kissqy/6e1f336083450cd9161193560f75c88dc0c7ff02/versions/v260/firmware/deploy/audio/aic3104-i2c3.dts','5fab9e696f835c77f6dd1d923a910e49a8bf7572ba9bad51203f27af6730c696'),
}

# This commit is the src submodule of Radxa's 6.1.84-10 kernel package.
CODEC_SOURCE = 'https://raw.githubusercontent.com/radxa/kernel/e6d12a77c8bc0ea6decbd3e4604ec131d4032c30/sound/soc/codecs/'
CODEC_FILES = {
    'tlv320aic3x.c': 'd828977826122524fa05e91dcb3c9597b876c60cfe3309254e0e571f24c60b80',
    'tlv320aic3x-i2c.c': '216466b6990ddb5a07f867053bb3d459a31152a4e6dd08585bbff4c149323955',
    'tlv320aic3x.h': '0c1db577de263b7c8161ed9069973385fbf45a0b4a075a582d4dea21ccfde186',
}
SOURCES.update({name: (CODEC_SOURCE + name, digest) for name, digest in CODEC_FILES.items()})
CODEC_DIR = '/usr/src/duckbase-aic3x-6.1.84'


def sha(path):
    with path.open('rb') as fp:
        return hashlib.file_digest(fp, 'sha256').hexdigest()

def run(*args, **kw):
    return subprocess.run(args, check=True, **kw)

def fetch(cache):
    cache.mkdir(exist_ok=True)
    for name, (url, expected) in SOURCES.items():
        path = cache / name
        if not path.exists():
            part = path.with_suffix(path.suffix+'.part')
            run('curl','-fsSL','--retry','3','-o',part,url)
            if sha(part) != expected:
                raise ValueError('download checksum: '+name)
            part.replace(path)
        if not path.is_file() or sha(path) != expected:
            raise ValueError('cache checksum: '+name)

def transfer(src, dst, count):
    digest = hashlib.sha256()
    while count:
        data = src.read(min(4 << 20, count))
        if not data:
            raise EOFError('partition truncated')
        if dst is not None:
            dst.write(data)
        digest.update(data)
        count -= len(data)
    return digest.hexdigest()

def build(out, work, console=None):
    motion_files = read_runtime(console, BOARD) if console is not None else {}
    os.chdir(work)
    cache = out/'cache'
    fetch(cache)
    raw = Path('system.img')
    with raw.open('xb') as f:
        run('xz','-dc',cache/'radxa.img.xz',stdout=f)
    with raw.open('rb') as f:
        f.seek(512)
        header = f.read(512)
        if header[:8] != b'EFI PART':
            raise ValueError('GPT header missing')
        entries, _, stride = struct.unpack_from('<QII', header, 72)
        regions = []
        for i,name in enumerate(('config.fat','efi','root.ext4')):
            f.seek(entries*512+i*stride+32)
            first,last = struct.unpack('<QQ', f.read(16))
            regions.append((first*512,(last-first+1)*512))
            if i != 1:
                f.seek(first*512)
                with open(name,'xb') as partition:
                    transfer(f,partition,regions[-1][1])
        if regions[0][0]!=32768*512 or regions[2][0]!=679936*512:
            raise ValueError('wrong board image partition layout')

    def debug(command):
        return run('debugfs','-R',command,'root.ext4',capture_output=True).stdout
    def read(path):
        temp = Path('readback')
        temp.unlink(missing_ok=True)
        debug('dump '+path+' readback')
        return temp.read_bytes()

    files = {}
    def add(path, data, mode=0o644):
        files[path] = (data.encode() if isinstance(data,str) else data, mode)

    files.update(motion_files)
    add('/opt/duck-base/check_calibration.py',(HERE/'check_calibration.py').read_bytes())
    for name in ('bus.py','sensors.py','base.py','home.py','remote.py','provision.py','duck-check'):
        add('/opt/duck-base/'+name,(BOARD/name).read_bytes(),0o755 if name=='duck-check' else 0o644)
    for name in ('control.py','ble.py','observation.py'):
        add('/opt/duck-base/'+name,(BRIDGE/name).read_bytes())
    for name in ('tof.whl','smbus.whl'):
        add('/opt/duck-base/'+name,(cache/name).read_bytes())
    for name in CODEC_FILES:
        add(CODEC_DIR+'/'+name, (cache/name).read_bytes())
    for name in ('Makefile','dkms.conf'):
        add(CODEC_DIR+'/'+name, (BOARD/'audio'/name).read_bytes())
    add('/opt/duck-base/enable-codec', (BOARD/'audio/enable-codec').read_bytes(), 0o755)
    for name in ('duck-provision.service','duck-base.service','duck-audio.service'):
        add('/etc/systemd/system/'+name,(BOARD/name).read_bytes())
    add('/usr/lib/sysusers.d/duck-base.conf','u duckbase - "Duck sensor service" /var/lib/duck-base /usr/sbin/nologin\n')
    add('/etc/modules-load.d/duck-base.conf','i2c-dev\n')
    add('/etc/ssh/sshd_config.d/01-duck-base.conf','PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\nAuthorizedKeysFile /etc/ssh/duck-keys/%u\nAllowUsers radxa\n')
    # Duck Base serves telemetry; querying the phone's protected GATT services is unnecessary.
    bluetooth_conf = read('/etc/bluetooth/main.conf')
    general = b'[General]\n'
    if bluetooth_conf.count(general) != 1 or b'\nReverseServiceDiscovery' in bluetooth_conf:
        raise ValueError('unexpected stock BlueZ configuration')
    add('/etc/bluetooth/main.conf', bluetooth_conf.replace(general, general + b'ReverseServiceDiscovery = false\n'))
    add('/etc/systemd/system/duck-home.service', (BOARD/'duck-home.service').read_bytes())
    for template in ('remote.toml', 'full-control.conf'):
        add('/opt/duck-base/'+template, (BOARD/template).read_bytes())
    add('/opt/duck-base/home.toml', (BOARD/'home.toml').read_bytes())
    add('/etc/hostname','duckbase\n')
    add('/etc/hosts',read('/etc/hosts').replace(b'radxa-zero3',b'duckbase'))

    overlay_names = ['rk3568-uart2-m0','rk3568-i2c3-m0','radxa-zero3-rpi-camera-v2','duck-hat','duck-audio']
    overlays = [name+'.dtbo' for name in overlay_names]
    for name in overlays[:3]:
        Path(name).write_bytes(read('/boot/dtbo/'+name+'.disabled'))
    for source,name in zip([BOARD/'hat.dts',cache/'audio.dts'],overlays[3:]):
        run('dtc','-@','-I','dts','-O','dtb','-o',name,source)
    for name in overlays:
        add('/boot/dtbo/'+name,Path(name).read_bytes())
    variants = {}
    for wifi in ('ap6212','aic8800ds2'):
        Path('board.dtb').write_bytes(read(f'/usr/lib/linux-image-{KERNEL}/rockchip/rk3566-radxa-zero-3w-{wifi}.dtb'))
        run('fdtoverlay','-i','board.dtb','-o','merged.dtb',*overlays)
        def prop(node, key):
            return run('fdtget','merged.dtb',node,key,capture_output=True,text=True).stdout.strip()
        for key in ('uart2','i2c3','i2s3_2ch'):
            if prop(prop('/__symbols__',key),'status') != 'okay':
                raise ValueError('overlay not active: '+key)
        if prop('/i2c@fe5c0000/fusb302@22','status')!='disabled' or prop('/sound-aic3104','simple-audio-card,name')!='aic3104':
            raise ValueError('incomplete HAT device tree')
        variants[wifi]=sha(Path('merged.dtb'))
    def cmdline(text):
        return text.replace('console=ttyFIQ0,1500000n8 ','').replace(' earlycon ',' ')
    ext = cmdline(read('/boot/extlinux/extlinux.conf').decode()).replace('prompt 1','prompt 0').replace('timeout 10','timeout 1')
    anchor = f'fdtdir /usr/lib/linux-image-{KERNEL}/'
    if ext.count(anchor)!=2:
        raise ValueError('unexpected extlinux layout')
    add('/boot/extlinux/extlinux.conf',ext.replace(anchor,anchor+'\n\tfdtoverlays '+' '.join('/boot/dtbo/'+s for s in overlays)))
    add('/etc/kernel/cmdline',cmdline(read('/etc/kernel/cmdline').decode()))
    add('/etc/default/u-boot',read('/etc/default/u-boot').decode()+'\nU_BOOT_PROMPT="0"\nU_BOOT_TIMEOUT="1"\nU_BOOT_FDT_OVERLAYS="'+' '.join(overlays)+'"\n')
    # debugfs does not reliably propagate command errors via exit status. Read every file back.
    commands = []
    def exists(path):
        return b'Inode:' in debug('stat '+path)
    directories = {str(parent) for path in files for parent in Path(path).parents if str(parent) != '/'}
    directories.add('/etc/duck-base')
    for directory in sorted(directories, key=lambda path:(path.count('/'),path)):
        if not exists(directory):
            commands += ['mkdir '+directory,'set_inode_field '+directory+' mode 040755']
    for i,(path,(data,mode)) in enumerate(files.items()):
        name='asset'+str(i)
        Path(name).write_bytes(data)
        if exists(path):
            commands.append('rm '+path)
        commands += [f'write {name} {path}',f'set_inode_field {path} mode 0{0o100000|mode:o}',
                     'set_inode_field '+path+' uid 0','set_inode_field '+path+' gid 0']
    links = {'/usr/local/sbin/duck-check':'/opt/duck-base/duck-check',
             '/etc/systemd/system/serial-getty@ttyS2.service':'/dev/null',
             '/etc/systemd/system/serial-getty@ttyFIQ0.service':'/dev/null',
             '/etc/systemd/system/default.target':'/lib/systemd/system/multi-user.target'}
    if motion_files:
        links['/etc/systemd/system/multi-user.target.wants/duck-home.service'] = '../duck-home.service'
        links['/opt/duck-base/v260/voice-bank'] = '/var/lib/duck-voice'
    for unit in ('duck-base.service','duck-provision.service','duck-audio.service'):
        links['/etc/systemd/system/multi-user.target.wants/'+unit]='../'+unit
    for link,target in links.items():
        if exists(link):
            commands.append('rm '+link)
        commands.append(f'symlink {link} {target}')
    Path('edit').write_text('\n'.join(commands)+'\n')
    run('debugfs','-w','-f','edit','root.ext4',capture_output=True)
    for path,(data,mode) in files.items():
        if read(path)!=data or f'{mode:04o}'.encode() not in debug('stat '+path).splitlines()[0]:
            raise ValueError('failed file readback: '+path)
    for link,target in links.items():
        if target.encode() not in debug('stat '+link):
            raise ValueError('failed link readback: '+link)
    run('e2fsck','-fn','root.ext4')
    before = run('mtype','-i','config.fat','::before.txt',capture_output=True,text=True).stdout
    before = '\n'.join(row for row in before.splitlines() if not row.startswith(('add_user ','user_append_group ')) and row!='disable_service ssh')
    before=before.replace('if headless enable_service ssh','enable_service ssh')+'\n'
    fat_files = [('before.txt',before),('DUCKBASE.ID','duck-base-zero3w-v1\n')]
    if motion_files:
        fat_files.append(('DUCKMOT.ID',MARKER.decode()))
    for path, content in fat_files:
        Path(path).write_text(content)
        run('mcopy','-o','-i','config.fat',path,'::'+path)
        if run('mtype','-i','config.fat','::'+path,capture_output=True).stdout!=Path(path).read_bytes():
            raise ValueError('FAT readback: '+path)
    checks = {}
    with raw.open('r+b') as image:
        for slot,name in [(0,'config.fat'),(2,'root.ext4')]:
            offset,size=regions[slot]
            with open(name,'rb') as partition:
                image.seek(offset)
                expected=transfer(partition,image,size)
                image.flush()
                image.seek(offset)
                if transfer(image,None,size)!=expected:
                    raise ValueError('final partition readback: '+name)
                checks[name]=expected
        os.fsync(image.fileno())
    result = {'board':'Radxa ZERO 3W','release':'rsdk-b1','sources':SOURCES,
              'verified_files':{path:hashlib.sha256(data).hexdigest() for path,(data,_) in files.items()},
              'verified_links':links,'merged_device_trees':variants,'partition_readback':checks,
              'ext4_check':'clean','hardware_verified':False,'image_bytes':raw.stat().st_size,'image_sha256':sha(raw)}
    if motion_files:
        result['motion_runtime'] = {'service_build':BUILD,'firmware_sha256':ARCHIVE_SHA,'lease_sha256':LEASE_SHA,
                                    'calibration_required':True,'control_key_in_image':False}
    dest=out/('duck-base-zero3w-motion-v1.img' if motion_files else 'duck-base-zero3w-v1.img')
    raw.replace(dest)
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(result['image_sha256']+'  '+dest.name+'\n')
    print(dest, result['image_sha256'],flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--v260-console',type=Path,help='Import the verified motion runtime from this local V260 console')
    args=parser.parse_args()
    console=args.v260_console.resolve() if args.v260_console else None
    out=(args.out or (DEFAULT.with_name('zero3w-motion') if console else DEFAULT)).resolve()
    out.mkdir(parents=True,exist_ok=True)
    for prefix in ('/opt/homebrew/opt/e2fsprogs/sbin','/usr/local/opt/e2fsprogs/sbin'):
        if Path(prefix).is_dir():
            os.environ['PATH']=prefix+os.pathsep+os.environ['PATH']
    for exe in ('xz','curl','dtc','fdtoverlay','fdtget','mtype','mcopy','e2fsck','debugfs'):
        if not shutil.which(exe):
            parser.error('missing tool: '+exe)
    with tempfile.TemporaryDirectory(dir=out,prefix='build-') as work:
        build(out,work,console)
