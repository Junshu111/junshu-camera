#!/usr/bin/env python3
"""Pi-only real-camera entry point. Never starts a simulated production camera."""
import fcntl,logging,os,signal,sys
from pathlib import Path
VERSION='1.0.0'

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv==['--version']:print('New Cam '+VERSION);return 0
    l1_enabled=argv!=['--no-l1']
    if argv and l1_enabled:print('用法：python3 run_camera.py [--version | --no-l1]',file=sys.stderr);return 2
    if sys.platform!='linux':print('此软件需要树莓派 Linux 和真实 CM2；Mac 不能作为相机运行。',file=sys.stderr);return 2
    if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        print('请在树莓派本地桌面启动；普通 SSH 没有触屏显示会话。',file=sys.stderr);return 2
    root=Path.home();state=root/'.local/state/junshu-camera';state.mkdir(parents=True,exist_ok=True)
    # Share the legacy instance lock so 0.2.0 and R4 cannot own CM2 together.
    lock=(state/'instance.lock').open('a+')
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();print('相机程序已经运行，请先在相机上退出旧版。',file=sys.stderr);return 2
    log_path=state/'camera-newcam-1.0.log'
    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(log_path,encoding='utf-8'),logging.StreamHandler()],format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.info('New Cam %s; no auto-cleanup; photos preserved',VERSION)
    camera=None;app=None;l1=None;power_client=None;splash=None;window=None
    try:
        from PyQt5.QtCore import Qt,QTimer
        from PyQt5.QtWidgets import QApplication
        from camera_r4.loading import create_loading_window,StartupGate
        # Display the approved image before importing/initializing Picamera2.
        QApplication.setAttribute(Qt.AA_SynthesizeMouseForUnhandledTouchEvents,False)
        app=QApplication([sys.argv[0]]);app.setApplicationName('New Cam 1.0.0')
        splash=create_loading_window(Path(__file__).resolve().parent/'assets/boot-logo.png')
        splash.showFullScreen();app.processEvents()
        from camera_r4.camera import PiCamera
        from camera_r4.storage import JPEGStore
        from camera_r4.gui import CameraWindow
        from camera_r4.l1_input import L1Input
        from camera_r4.power_client import PowerClient
        # CameraWindow consumes native touch. Never synthesize a second mouse shot.
        store=JPEGStore(root/'Pictures/JunshuCamera/photos');camera=PiCamera()
        l1=L1Input() if l1_enabled else None
        power_client=PowerClient()
        window=CameraWindow(camera,store,root/'.config/junshu-camera/settings-r4.json',l1=l1,power_client=power_client)
        screen=app.primaryScreen();logging.info('Qt platform=%s screen=%s geometry=%s DPR=%s',app.platformName(),screen.name(),screen.geometry(),screen.devicePixelRatio())
        camera.start()
        if l1 is not None:l1.start()
        gate=StartupGate(window,camera,splash,app)
        signal.signal(signal.SIGINT,lambda *args:window.request_exit(confirmed=True,system_signal=True))
        signal.signal(signal.SIGTERM,lambda *args:window.request_exit(confirmed=True,system_signal=True))
        signal_tick=QTimer()
        logged_failure=[False]
        def startup_tick():
            gate.poll()
            if gate.failure_reason and not logged_failure[0]:
                logging.error('startup handoff failed: %s',gate.failure_reason)
                logged_failure[0]=True
        signal_tick.timeout.connect(startup_tick);signal_tick.start(33)
        return app.exec_()
    except Exception as error:
        logging.exception('startup failed');print('启动失败：{}；日志：{}'.format(error,log_path),file=sys.stderr)
        if splash is not None:splash.close()
        if camera is not None:
            try:camera.close()
            except Exception:logging.exception('camera close after startup failure')
        return 1
    finally:
        if power_client is not None:power_client.close()
        if l1 is not None:
            try:l1.close()
            except Exception:logging.exception('L1 release not confirmed')
        lock.close()

if __name__=='__main__':sys.exit(main())
