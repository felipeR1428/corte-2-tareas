import pybullet as p
import pybullet_data
import time

# Conectar a PyBullet
physics_client = p.connect(p.GUI)
p.setAdditionalSearchPath(pybullet_data.getDataPath())

# Cargar el robot
robot_id = p.loadURDF("brazo.urdf", [0, 0, 0.15], useFixedBase=True)

# Verificar articulaciones
num_joints = p.getNumJoints(robot_id)
print(f"Articulaciones encontradas: {num_joints}")

for i in range(num_joints):
    info = p.getJointInfo(robot_id, i)
    print(f"Joint {i}: {info[1].decode('utf-8')} (tipo: {info[2]})")

# Mantener abierto
while True:
    p.stepSimulation()
    time.sleep(0.05)