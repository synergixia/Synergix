import asyncio

# Semáforo global: Limita a un máximo de 2 inferencias simultáneas.
# Esto asegura que los 4 núcleos del servidor Hetzner 8GB no se saturen
# cuando múltiples usuarios hablan al mismo tiempo.
sem = asyncio.Semaphore(2)
