"""Ejecuta los servicios como procesos para pruebas de software reproducibles.

No crea contenedores, VLAN, interfaces físicas ni resultados de aislamiento.
Requiere Python con requirements-local.txt, Mosquitto y los URDF indicados en README.
"""
import argparse
import getpass
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

class Laboratorio:
    def __init__(self, modelos, broker='mosquitto', output=None):
        self.modelos = str(Path(modelos).resolve())
        self.broker = broker
        self.output = Path(output or ROOT/'resultados'/'prueba-local').resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.files = []
        self.env = {**os.environ, 'ADMIN_IP':'127.0.0.1', 'MQTT_HOST':'127.0.0.1',
                    'MQTT_PUERTO':'18883', 'HB_PUERTO':'15300', 'HTTP_PUERTO':'18180',
                    'MODELOS':self.modelos, 'RESULTADOS':str(self.output),
                    'RESULTADOS_DIR':str(self.output), 'PYTHONUNBUFFERED':'1',
                    'ROUTER_IP':'', 'RUTAS':'', 'NEXO_MODO':'software',
                    'OBJETIVOS':'eco-admin-local=127.0.0.1', 'NEXO_SONDEO':'udp', 'NEXO_ECO_PUERTO':'15300',
                    'NEXO_VISORES': json.dumps({
                        'pista':'http://127.0.0.1:18010','spot':'http://127.0.0.1:18011',
                        'pepper':'http://127.0.0.1:18012','nao':'http://127.0.0.1:18013'}),
                    'NEXO_CONTROLES':json.dumps({r:'http://127.0.0.1:19090' for r in
                        ['ctrl-1','ctrl-2','ctrl-3','ctrl-spot','ctrl-pepper','ctrl-nao','esclava']})}
    def start(self, name, args, env=None):
        if name in self.processes and self.processes[name].poll() is None:
            raise RuntimeError(name+' ya está ejecutándose')
        f=(self.output/(name+'.log')).open('ab');self.files.append(f)
        p=subprocess.Popen(args,cwd=ROOT,env={**self.env,**(env or {})},stdout=f,stderr=subprocess.STDOUT,
                           start_new_session=True)
        self.processes[name]=p
        return p
    def stop(self,name):
        p=self.processes.get(name)
        if p is None or p.poll() is not None:return
        if os.name=='nt':p.terminate()
        else:os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=12)
        except subprocess.TimeoutExpired:
            if os.name=='nt':p.kill()
            else:os.killpg(p.pid,signal.SIGKILL)
            p.wait(timeout=3)
    def start_robot(self, kind):
        port={'spot':(15101,18011),'pepper':(15102,18012),'nao':(15103,18013)}[kind]
        return self.start('sim-'+kind,[sys.executable,'zona-robotica/sim_robot.py'],
             {'ROBOT':kind,'PUERTO_UDP':str(port[0]),'PUERTO_HTTP':str(port[1]),'CUADROS_FPS':'6'})
    def start_all(self):
        cfg=self.output/'mosquitto.conf'
        # El broker de prueba solo admite conexiones locales; no usa el puerto de la placa física.
        usuario = ('user '+getpass.getuser()+'\n') if os.name != 'nt' else ''
        cfg.write_text('listener 18883 127.0.0.1\nallow_anonymous true\npersistence false\n'+usuario+'log_type warning\n')
        self.start('broker',[self.broker,'-c',str(cfg)])
        self.start('admin',[sys.executable,'plano-admin/admin/monitor.py'])
        self.start('track-server',[sys.executable,'zona-gamer/servidor-pista/servidor_pista.py'],
                   {'PUERTO_WS':'18765','PUERTO_HTTP':'18010','FPS_VIDEO':'6'})
        for i in (1,2,3):
            self.start('player-'+str(i),[sys.executable,'zona-gamer/jugador/jugador.py'],
                       {'JUGADOR':str(i),'PUERTO_UDP':str(15000+i),'SERVIDOR_WS':'ws://127.0.0.1:18765',
                        'NOMBRE':['','Coral','Océano','Bosque'][i],
                        'COLOR':['','218,121,67','32,152,172','83,149,93'][i]})
        for kind in ('spot','pepper','nao'):self.start_robot(kind)
        self.start('esp32-emuladas',[sys.executable,'emulador/emulador_esp32.py',
            '--rol','ctrl-1','ctrl-2','ctrl-3','ctrl-spot','ctrl-pepper','ctrl-nao','esclava',
            '--destino','127.0.0.1:15001','127.0.0.1:15002','127.0.0.1:15003',
            '127.0.0.1:15101','127.0.0.1:15102','127.0.0.1:15103','--admin','127.0.0.1',
            '--puerto-admin','15300','--mqtt','127.0.0.1:18883','--piloto',
            '--pista','ws://127.0.0.1:18765','--http','19090','--hz','20',
            '--resumen',str(self.output/'esp32_resumen.json')])
    def read(self,path='/api/resumen.json'):
        with HTTP.open('http://127.0.0.1:18180'+path,timeout=4) as r:return json.load(r)
    def wait_ready(self,timeout=90):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                data=self.read()
                if data['mqtt_conectado'] and all(data['servicios'][n]['hb'] for n in
                      ['track-server','player-1','player-2','player-3','sim-spot','sim-pepper','sim-nao']):
                    for n in ('pista','spot','pepper','nao'):
                        data2=self.read('/api/zona/'+n+'/estado')
                        if not data2:raise ValueError('visor iniciando')
                        with HTTP.open('http://127.0.0.1:18180/api/zona/'+n+'/cuadro',timeout=4) as image:
                            if not image.read(2)==b'\xff\xd8':raise ValueError('render no disponible')
                    return data
            except (OSError,ValueError,KeyError):pass
            dead={n:p.returncode for n,p in self.processes.items() if p.poll() is not None}
            if dead:raise RuntimeError('Procesos terminados: '+str(dead)+'; revisar '+str(self.output))
            time.sleep(.5)
        raise TimeoutError('Los servicios no estuvieron listos: '+str(self.output))
    def close(self):
        for name in list(self.processes)[::-1]:self.stop(name)
        for f in self.files:f.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--modelos',required=True);p.add_argument('--broker',default='mosquitto')
    p.add_argument('--segundos',type=int,default=0)
    args=p.parse_args();lab=Laboratorio(args.modelos,args.broker)
    try:
        lab.start_all();lab.wait_ready();print('NexoLab de SOFTWARE: http://127.0.0.1:18180',flush=True)
        if args.segundos:time.sleep(args.segundos)
        else:
            while True:time.sleep(1)
    except KeyboardInterrupt:pass
    finally:lab.close()
if __name__=='__main__':main()
