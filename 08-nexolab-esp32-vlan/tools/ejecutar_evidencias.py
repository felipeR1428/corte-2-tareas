"""Orquesta las pruebas de software y la captura del navegador en la misma sesión."""
import argparse,json,os,subprocess,sys,time,shutil
from pathlib import Path
from laboratorio_local import Laboratorio,ROOT

def archivar_registros(output):
    """Conserva los datos de la sesión junto a sus capturas y videos."""
    destino=ROOT/'evidencias'/'datos'
    logs=destino/'logs';logs.mkdir(parents=True,exist_ok=True)
    for nombre in ('admin_metricas.csv','eventos.jsonl','esp32_resumen.json'):
        origen=output/nombre
        if origen.is_file():shutil.copy2(origen,destino/nombre)
    for origen in output.glob('*.log'):
        shutil.copy2(origen,logs/origen.name)

def main():
    p=argparse.ArgumentParser();p.add_argument('--modelos',required=True);p.add_argument('--broker',default='mosquitto')
    p.add_argument('--script',default='tools/grabar_evidencias.js');args=p.parse_args()
    output=ROOT/'resultados'/'sesion-evidencias'
    if output.exists():shutil.rmtree(output)
    lab=Laboratorio(args.modelos,args.broker,output)
    try:
        lab.start_all();lab.wait_ready();print('SERVICIOS_LISTOS',flush=True)
        proc=subprocess.Popen(['node',args.script],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,bufsize=1)
        for line in proc.stdout:
            print(line.strip(),flush=True)
            if line.startswith('NEXO_EVENT '):
                event=json.loads(line[len('NEXO_EVENT '):])
                if event=={'action':'stop','service':'sim-pepper'}:lab.stop('sim-pepper')
                elif event=={'action':'start','service':'sim-pepper'}:lab.start_robot('pepper')
                else:raise ValueError('Acción de prueba no permitida')
        if proc.wait()!=0:raise RuntimeError('Falló la prueba del navegador')
    finally:lab.close()
    archivar_registros(output)
    subprocess.run([sys.executable,str(ROOT/'tools'/'crear_animaciones.py')],check=True)
if __name__=='__main__':main()
