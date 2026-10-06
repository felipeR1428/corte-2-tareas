"""Invariantes geométricas del circuito usado por física, render y piloto."""
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'zona-gamer'/'servidor-pista'))
import pista as P

class CircuitoGP(unittest.TestCase):
    def test_cierre_sin_salto(self):
        a=P.punto(0);b=P.punto(P.PERIMETRO)
        self.assertLess(math.dist(a[:2],b[:2]),1e-10)
        self.assertLess(math.dist(P.punto(-.001)[:2],P.punto(.001)[:2]),.003)
    def test_proyeccion_del_centro(self):
        for i in range(200):
            s=i*P.PERIMETRO/200;x,y,_=P.punto(s);ss,d=P.proyectar(x,y)
            self.assertLess(abs(P.envolver_delta(s-ss)),.002)
            self.assertLess(abs(d),.002)
    def test_proyeccion_de_carriles(self):
        for i in range(150):
            for d in [-.4,.4]:
                s=i*P.PERIMETRO/150;x,y,_=P.punto(s,d);ss,dd=P.proyectar(x,y)
                self.assertLess(abs(P.envolver_delta(s-ss)),.045)
                self.assertAlmostEqual(dd,d,delta=.015)
    def test_curvas_en_ambos_sentidos(self):
        cur=[P.curvatura(i*P.PERIMETRO/500) for i in range(500)]
        self.assertGreater(sum(x>.2 for x in cur),50)
        self.assertGreater(sum(x<-.2 for x in cur),35)
        # El borde no debe plegarse hacia atrás en las curvas cerradas.
        self.assertLess(max(map(abs,cur))*(P.ANCHO/2+P.GROSOR_MURO/2),1)
    def test_muestreo_para_clientes(self):
        pts=P.linea_central(.25)
        self.assertGreater(P.PERIMETRO,70)
        self.assertTrue(all(math.dist(a,b)<.27 for a,b in zip(pts,pts[1:]+pts[:1])))
    def test_malla_gt_valida(self):
        lines=Path(P.malla_gt()).read_text().splitlines()
        n=sum(s.startswith('v ') for s in lines)
        faces=[s.split()[1:] for s in lines if s.startswith('f ')]
        self.assertGreater(n,30);self.assertGreater(len(faces),50)
        self.assertTrue(all(1<=int(v)<=n for face in faces for v in face))

if __name__=='__main__':unittest.main()
