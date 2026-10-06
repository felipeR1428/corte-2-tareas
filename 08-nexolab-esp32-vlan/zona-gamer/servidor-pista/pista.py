"""Circuito Nexo GP: trazado cerrado de curvas enlazadas y coches de competición.

La geometría de longitud de arco se comparte entre colisiones, render, pilotos y
conteo de vueltas. Las carrocerías visuales mantienen la mecánica del RACECAR.
"""
from __future__ import annotations
import math
import os
import tempfile
import bisect
import numpy as np
import pybullet as p
import pybullet_data

NOMBRE = "Circuito Nexo GP"
ANCHO = 1.8
ALTO_MURO, GROSOR_MURO, ANCHO_PIANO = .16, .09, .16
LARGO_RECTA, RADIO = 15.0, 0.0  # Compatibilidad; este circuito no es un óvalo de radio constante.
# Recta de salida, horquilla oriental, eses centrales, curva norte y retorno oeste.
CONTROL = [(0,-6),(5,-6),(9,-5.8),(11.5,-4),(12,-1),(10.4,1.1),
           (8,1),(6.4,-1.2),(4,-2.1),(1.8,-.4),(2.7,2),(5.2,3.7),
           (4.4,6),(1,6.5),(-2.2,6),(-4.8,4.1),(-4.8,1.8),
           (-3.8,-.3),(-6.2,-1.6),(-8.7,.1),(-10,2.3),(-12.5,2),
           (-13,-1.4),(-11.5,-4.5),(-8,-6),(-4,-6)]

def _trazado():
    pts=[];n=len(CONTROL)
    for i in range(n):
        a,b,c,d=[np.array(CONTROL[(i+k)%n],dtype=float) for k in (-1,0,1,2)]
        for t in np.linspace(0,1,32,endpoint=False):
            pts.append(((1-t)**3*a+(3*t**3-6*t*t+4)*b+(-3*t**3+3*t*t+3*t+1)*c+t**3*d)/6)
    v=np.array(pts);ends=np.roll(v,-1,axis=0);delta=ends-v
    lengths=np.linalg.norm(delta,axis=1)
    return v,delta,lengths,np.concatenate([[0],np.cumsum(lengths)])
_VERT,_DELTA,_LENS,_ARC = _trazado()
_LEN2 = _LENS*_LENS
PERIMETRO=float(_ARC[-1])
CENTRO_CAMARA=(-.5,.2)
ALTO_VISTA=22.2
C_PASTO=(.25,.39,.27,1); C_ASFALTO=(.17,.19,.22,1)
C_LINEA=(.94,.94,.89,1); C_PIANO=((.88,.16,.13,1),(.97,.96,.91,1))
Z_ASFALTO,Z_PIANO,Z_LINEA=.004,.008,.011

def envolver(s):return s%PERIMETRO

def envolver_delta(ds):return (ds+PERIMETRO/2)%PERIMETRO-PERIMETRO/2

def punto(s,d=0.0):
    s=envolver(s);i=min(len(_LENS)-1,bisect.bisect_right(_ARC,s)-1)
    t=(s-_ARC[i])/_LENS[i];xy=_VERT[i]+t*_DELTA[i]
    dx,dy=_DELTA[i]/_LENS[i]
    return float(xy[0]-d*dy),float(xy[1]+d*dx),math.atan2(dy,dx)

def proyectar(x,y):
    q=np.array([x,y])-_VERT
    t=np.clip(np.einsum('ij,ij->i',q,_DELTA)/_LEN2,0,1)
    diff=q-t[:,None]*_DELTA
    i=int(np.argmin(np.einsum('ij,ij->i',diff,diff)))
    d=(_DELTA[i,0]*diff[i,1]-_DELTA[i,1]*diff[i,0])/_LENS[i]
    return envolver(float(_ARC[i]+t[i]*_LENS[i])),float(d)

def curvatura(s):
    a=punto(s-.25)[2];b=punto(s+.25)[2]
    return math.atan2(math.sin(b-a),math.cos(b-a))/.5

def linea_central(paso=.25):
    n=max(4,int(round(PERIMETRO/paso)))
    return [[round(v,3) for v in punto(i*PERIMETRO/n)[:2]] for i in range(n)]

def muestras_borde():
    n=math.ceil(PERIMETRO/.65)
    return [i*PERIMETRO/n for i in range(n+1)]

def en_curva(s):return abs(curvatura(s))>.08

def caja(medias,pos,rgba=None,yaw=0,visual=True,colision=False):
    vis=p.createVisualShape(p.GEOM_BOX,halfExtents=medias,rgbaColor=rgba) if visual else -1
    col=p.createCollisionShape(p.GEOM_BOX,halfExtents=medias) if colision else -1
    return p.createMultiBody(0,col,vis,pos,p.getQuaternionFromEuler([0,0,yaw]))

def _tramos_borde(d):
    ss=muestras_borde()
    for k,(a,b) in enumerate(zip(ss,ss[1:])):
        xa,ya,_=punto(a,d);xb,yb,_=punto(b,d)
        yield k,(a+b)/2,((xa+xb)/2,(ya+yb)/2),math.hypot(xb-xa,yb-ya),math.atan2(yb-ya,xb-xa)

def _cinta(ancho,z,color):
    ss=muestras_borde();v=[];indices=[]
    for s in ss:
        v.extend([[*punto(s,ancho/2)[:2],z],[*punto(s,-ancho/2)[:2],z]])
    for i in range(len(ss)-1):
        j=2*i;indices.extend([j,j+1,j+2,j+1,j+3,j+2])
    vis=p.createVisualShape(p.GEOM_MESH,vertices=v,indices=indices,rgbaColor=color)
    p.createMultiBody(0,-1,vis)

def construir_pista(visual=True,colision=True):
    piso=caja((19,12,.05),(0,0,-.05),C_PASTO,visual=visual,colision=colision)
    if colision:p.changeDynamics(piso,-1,lateralFriction=1)
    for lado in (-1,1):
        for k,_,(cx,cy),largo,rumbo in _tramos_borde(lado*(ANCHO/2+GROSOR_MURO/2)):
            color=(.74,.79,.77,1) if k%8 else (.10,.38,.39,1)
            caja((largo/2+.04,GROSOR_MURO/2,ALTO_MURO/2),(cx,cy,ALTO_MURO/2),color,rumbo,visual,colision)
    if not visual:return
    # Franjas del césped, escapatoria pintada y asfalto continuo, sin huecos entre segmentos.
    for y in range(-10,11,2):caja((18,.5,.0005),(0,y,.0005),(.28,.43,.29,1))
    _cinta(ANCHO+.38,.003,(.29,.61,.55,1));_cinta(ANCHO,Z_ASFALTO,C_ASFALTO)
    for lado in (-1,1):
        for k,_,(cx,cy),largo,rumbo in _tramos_borde(lado*(ANCHO/2-ANCHO_PIANO/2)):
            caja((largo/2+.02,ANCHO_PIANO/2,.001),(cx,cy,Z_PIANO),C_PIANO[k%2],rumbo)
    # Meta y cajones de parrilla, orientados mediante la propia geometría.
    for i in range(12):
        for j in range(3):
            x,y,a=punto((j-1)*.16,-ANCHO/2+(i+.5)*ANCHO/12)
            caja((.08,ANCHO/24,.001),(x,y,Z_LINEA),C_LINEA if (i+j)%2 else (.025,.03,.04,1),a)
    for i in range(6):
        s=-1-.75*i;d=.45 if i%2==0 else -.45
        for delta in (-.23,.23):
            x,y,a=punto(s+delta,d);caja((.013,.22,.001),(x,y,Z_LINEA),C_LINEA,a)
    # Boxes visuales junto a la recta y edificios de control.
    caja((5.3,.60,.002),(-1,-8.2,.003),(.16,.18,.21,1))
    for i in range(7):
        x=-5.2+i*1.4
        caja((.56,.58,.38),(x,-9.4,.38),(.79,.82,.79,1))
        caja((.62,.65,.045),(x,-9.4,.79),(.10,.31,.35,1))
        caja((.42,.015,.22),(x,-8.80,.29),[(.8,.25,.15,1),(.10,.48,.6,1),(.23,.5,.35,1)][i%3])
    caja((.7,.7,1.05),(7.5,-8.9,1.05),(.70,.73,.70,1))
    caja((.74,.74,.18),(7.5,-8.9,2.05),(.10,.29,.36,1))
    # Gradas escalonadas por fuera de los márgenes; sin efectos sobre los mandos.
    for x0,y0 in [(-6.6,8.5),(9.8,5.5),(-15,0)]:
        for row in range(3):
            caja((1.6,.2,.12*(row+1)),(x0,y0+row*.35,.12*(row+1)),(.59,.65,.66,1))
            for col in range(10):
                caja((.10,.11,.045),(x0-1.4+col*.30,y0+row*.35,.24*(row+1)+.045),(.12,.42,.50,1))
    # Torres, árboles y luminarias fuera de la banda de pista.
    for x,y in [(-8,5.3),(-7,4.6),(0,3.9),(.6,3.8),(-.4,-3.2),(8.8,4),(14,-4),(-15,-5)]:
        caja((.065,.065,.33),(x,y,.33),(.35,.24,.13,1))
        v=p.createVisualShape(p.GEOM_SPHERE,radius=.39,rgbaColor=(.13,.29,.17,1))
        p.createMultiBody(0,-1,v,[x,y,.8])
    for x,y in [(-8,-8),(3,-8),(10,-7),(-10,5),(3,8)]:
        caja((.025,.025,.75),(x,y,.75),(.65,.70,.70,1))
        caja((.16,.08,.025),(x,y,1.51),(.96,.94,.72,1))

# ---------------------------------------------------------------------------------------------
# El carro: racecar de pybullet_data
# ---------------------------------------------------------------------------------------------
# Juntas del racecar.urdf (se listaron con p.getJointInfo):
#   0 base_link_joint (fija)        1 chassis_inertia_joint (fija; aquí está la masa: 4 kg)
#   2 left_rear_wheel_joint         3 right_rear_wheel_joint        -> TRACCIÓN (control de velocidad)
#   4 left_steering_hinge_joint     6 right_steering_hinge_joint    -> DIRECCIÓN (control de posición)
#   5 left_front_wheel_joint        7 right_front_wheel_joint       -> ruedas delanteras LIBRES
#   8 hokuyo (lidar), 9-11 cámara ZED (fijas, solo adorno)
RUEDAS_TRACCION = (2, 3)
RUEDAS_LIBRES = (5, 7)
DIRECCION = (4, 6)
JUNTAS_MOVILES = (2, 3, 4, 5, 6, 7)     # las que la cámara necesita copiar para dibujar el carro
RADIO_RUEDA = 0.05
DISTANCIA_EJES = 0.325
GIRO_MAX = 0.5          # rad (~29 grados) de dirección a fondo; el URDF permite 1 rad, pero con más
                        # de ~0,5 las ruedas delanteras empiezan a "arrastrarse" de lado
VEL_MAX = 2.5           # m/s con el acelerador al 100 % (unos 9 km/h: un carro RC 1:10 rápido)
FUERZA_RUEDA = 6.0      # N*m máximos que el motor de cada rueda puede hacer para llegar a la velocidad

_URDF = {}


def urdf_carro(liviano: bool = False) -> str:
    """Ruta a una copia modificada del racecar.urdf de pybullet_data (se escribe en /tmp).

    Cambios, siempre:
      - Una caja de COLISIÓN en el chasis. El URDF original solo tiene colisión en las ruedas y en
        el lidar: el chasis es solo un dibujo, así que dos carros podían quedar "metidos" uno dentro
        del otro al chocar. La caja mide como el chasis (0,45 x 0,18 x 0,08 m) y empieza 5 cm sobre
        el piso (no roza el asfalto).
      - Las mallas con ruta absoluta (la copia vive en otra carpeta).
    Con liviano=True (solo para el proceso de la cámara, que no simula):
      - Las ruedas se dibujan como cilindros (su misma forma de colisión) y se quitan las mallas de
        las bisagras de dirección y del lidar. Esas mallas suman ~11 000 triángulos por carro y el
        renderizador por software transforma TODOS los triángulos de la escena en cada foto, aunque
        el carro quede fuera del recorte: con 6 carros eran ~12 ms fijos por llamada. Desde arriba
        (un carro mide ~25 píxeles) y en la cámara de persecución la diferencia no se nota.
    """
    import copy
    import xml.etree.ElementTree as ET

    if liviano in _URDF and os.path.exists(_URDF[liviano]):
        return _URDF[liviano]
    carpeta = os.path.join(pybullet_data.getDataPath(), "racecar").replace("\\", "/")
    arbol = ET.parse(os.path.join(carpeta, "racecar.urdf"))
    robot = arbol.getroot()
    for malla in robot.iter("mesh"):
        malla.set("filename", f"{carpeta}/{malla.get('filename')}")
    for link in robot.findall("link"):
        nombre = link.get("name")
        if nombre == "chassis":
            col = ET.SubElement(link, "collision")
            ET.SubElement(col, "origin", rpy="0 0 0", xyz="0.16 0 0.04")
            ET.SubElement(ET.SubElement(col, "geometry"), "box", size="0.45 0.18 0.08")
        if not liviano:
            continue
        vis = link.find("visual")
        if vis is None:
            continue
        if nombre == "chassis":
            geo=vis.find("geometry")
            for child in list(geo):geo.remove(child)
            ET.SubElement(geo,"mesh",filename=malla_gt())
            origin=vis.find("origin")
            if origin is not None:origin.set("xyz","0 0 0");origin.set("rpy","0 0 0")
        elif nombre.endswith("_wheel"):
            col = link.find("collision")
            link.remove(vis)
            vis = ET.SubElement(link, "visual")
            vis.append(copy.deepcopy(col.find("origin")))
            vis.append(copy.deepcopy(col.find("geometry")))
            mat = ET.SubElement(vis, "material", name="goma")
            ET.SubElement(mat, "color", rgba="0.07 0.07 0.08 1")
        elif nombre.endswith("_steering_hinge"):
            link.remove(vis)
        elif nombre == "laser" or "zed" in nombre.lower() or "camera" in nombre.lower():
            link.remove(vis)
    fd, ruta = tempfile.mkstemp(prefix="racecar_liviano_" if liviano else "racecar_", suffix=".urdf")
    os.close(fd)
    arbol.write(ruta, encoding="utf-8", xml_declaration=True)
    _URDF[liviano] = ruta
    return ruta


def cargar_carro(x: float, y: float, rumbo: float, color, liviano: bool = False, cli: int = 0) -> int:
    """Carga un racecar parado en (x, y) mirando hacia `rumbo` y le pinta el chasis.
    cli = en qué conexión de PyBullet (el proceso de la cámara usa dos)."""
    cid = p.loadURDF(urdf_carro(liviano), [x, y, 0.0], p.getQuaternionFromEuler([0, 0, rumbo]),
                     physicsClientId=cli)
    # El chasis es el link 0 ("chassis", la malla azul). changeVisualShape cambia el color solo en
    # la imagen; no toca la física.
    p.changeVisualShape(cid, 0, rgbaColor=list(color) + [1] if len(color) == 3 else list(color),
                        physicsClientId=cli)
    return cid


# Carrocería GT, ventanas, splitter, luces y alerón. Las piezas son visuales;
# la mecánica usa las ruedas, bisagras y caja de colisión del RACECAR.
NEGRO=(.035,.045,.055,1);BLANCO=(.95,.96,.92,1);CRISTAL=(.09,.21,.28,1)
_BASE_GT=[
 ((.235,.112,.009),(.165,0,.008),NEGRO),
 ((.087,.073,.022),(.12,0,.097),CRISTAL),
 ((.055,.066,.008),(.105,0,.125),"carro"),
 ((.045,.13,.009),(-.055,0,.138),NEGRO),
 ((.008,.010,.036),(-.042,-.075,.10),NEGRO),
 ((.008,.010,.036),(-.042,.075,.10),NEGRO),
 ((.045,.008,.025),(-.055,.133,.144),"carro"),
 ((.045,.008,.025),(-.055,-.133,.144),"carro"),
 ((.023,.145,.006),(.39,0,.014),NEGRO),
 ((.045,.008,.009),(.275,.04,.071),BLANCO),
 ((.045,.008,.009),(.275,-.04,.071),BLANCO),
 ((.035,.008,.009),(.01,.04,.078),BLANCO),
 ((.035,.008,.009),(.01,-.04,.078),BLANCO),
 ((.006,.026,.008),(.382,.065,.052),(.88,.96,1,1)),
 ((.006,.026,.008),(.382,-.065,.052),(.88,.96,1,1)),
 ((.006,.03,.006),(-.064,.073,.055),(.97,.045,.025,1)),
 ((.006,.03,.006),(-.064,-.073,.055),(.97,.045,.025,1)),
 ((.042,.005,.018),(.15,.128,.043),NEGRO),
 ((.042,.005,.018),(.15,-.128,.043),NEGRO)]
ADORNOS={name:list(_BASE_GT) for name in ('clasico','deportivo','rally','auto')}
ADORNOS['deportivo']+=[((.045,.008,.012),(.108,0,.138),BLANCO)]
ADORNOS['rally']+=[((.04,.022,.005),(.108,0,.138),NEGRO)]
ESTILOS=("clasico","deportivo","rally")

_MALLA_GT=None
def malla_gt():
    """Malla nativa pequeña de carrocería, sin descargas de modelos adicionales."""
    global _MALLA_GT
    if _MALLA_GT and os.path.exists(_MALLA_GT):return _MALLA_GT
    rings=[(-.07,.096,.048),(-.025,.124,.071),(.095,.128,.075),
           (.215,.125,.078),(.325,.117,.061),(.398,.085,.045)]
    vertices=[]
    for x,w,h in rings:
        vertices += [(x,-w,.012),(x,w,.012),(x,w,h*.80),(x,w*.70,h),(x,-w*.70,h),(x,-w,h*.80)]
    faces=[]
    for i in range(len(rings)-1):
        for k in range(6):
            a=i*6+k;b=i*6+(k+1)%6;c=(i+1)*6+(k+1)%6;d=(i+1)*6+k
            faces.extend([(a,b,c),(a,c,d)])
    for ring,reverse in [(0,True),(len(rings)-1,False)]:
        ids=[ring*6+k for k in range(6)]
        if reverse:ids.reverse()
        for i in range(1,5):faces.append((ids[0],ids[i],ids[i+1]))
    fd,_MALLA_GT=tempfile.mkstemp(prefix="nexolab_gt_",suffix=".obj");os.close(fd)
    with open(_MALLA_GT,'w') as f:
        f.write('o Nexo_GT\n')
        for v in vertices:f.write('v '+' '.join(map(str,v))+'\n')
        for face in faces:f.write('f '+' '.join(str(i+1) for i in face)+'\n')
    return _MALLA_GT
