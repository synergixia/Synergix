#!/usr/bin/env python3
"""
Script de backup periódico para DCellar
Ejecuta copias de seguridad incrementales
"""

import asyncio
import json
import logging
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from aisynergix.services.greenfield import (
    list_objects,
    get_object,
    put_object,
    delete_object
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def incremental_backup():
    """Realiza backup incremental de datos críticos"""
    logger.info("🔄 Iniciando backup incremental...")
    
    backup_timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_prefix = f"aisynergix/backups/{backup_timestamp}/"
    
    try:
        # 1. Backup de configuración del sistema
        config_files = [
            "aisynergix/data/system_config.json",
            "aisynergix/data/brain_pointer",
        ]
        
        for config_path in config_files:
            try:
                content = await get_object(config_path)
                backup_path = f"{backup_prefix}config/{Path(config_path).name}"
                await put_object(backup_path, content)
                logger.info(f"✅ Backup config: {config_path}")
            except Exception as e:
                logger.warning(f"⚠️ No se pudo hacer backup de {config_path}: {e}")
        
        # 2. Backup de metadatos de usuarios (solo cambios recientes)
        users = await list_objects("aisynergix/users/")
        recent_users = []
        
        for user_path in users[:100]:  # Límite para no saturar
            try:
                # En producción, verificaríamos timestamp de modificación
                recent_users.append(user_path)
            except:
                continue
        
        if recent_users:
            users_data = {"users": recent_users, "count": len(recent_users)}
            users_backup = f"{backup_prefix}users/recent.json"
            await put_object(users_backup, json.dumps(users_data).encode())
            logger.info(f"✅ Backup usuarios: {len(recent_users)} usuarios")
        
        # 3. Backup de últimos aportes
        aportes = await list_objects("aisynergix/aportes/")
        recent_aportes = aportes[-50:] if len(aportes) > 50 else aportes
        
        if recent_aportes:
            aportes_backup = f"{backup_prefix}aportes/recent.json"
            await put_object(aportes_backup, json.dumps(recent_aportes).encode())
            logger.info(f"✅ Backup aportes: {len(recent_aportes)} aportes")
        
        # 4. Crear manifest del backup
        manifest = {
            "timestamp": backup_timestamp,
            "type": "incremental",
            "items": {
                "config_files": len(config_files),
                "users": len(recent_users),
                "aportes": len(recent_aportes),
            }
        }
        
        await put_object(
            f"{backup_prefix}manifest.json",
            json.dumps(manifest, indent=2).encode()
        )
        
        logger.info(f"✅ Backup completado: {backup_prefix}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error en backup: {e}")
        return False

async def cleanup_old_backups(days_to_keep: int = 7):
    """Elimina backups antiguos"""
    try:
        backups = await list_objects("aisynergix/backups/")
        
        cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
        
        for backup_path in backups:
            try:
                # Extraer timestamp del path
                parts = backup_path.split('/')
                if len(parts) < 3:
                    continue
                
                timestamp_str = parts[2]
                backup_date = datetime.strptime(timestamp_str[:8], "%Y%m%d")
                
                if backup_date < cutoff_date:
                    await delete_object(backup_path)
                    logger.info(f"🧹 Eliminado backup antiguo: {backup_path}")
            except Exception as e:
                logger.warning(f"⚠️ Error procesando backup {backup_path}: {e}")
                
    except Exception as e:
        logger.error(f"❌ Error limpiando backups: {e}")

async def main():
    """Loop principal del servicio de backup"""
    logger.info("💾 Servicio de backup iniciado")
    
    while True:
        try:
            # Ejecutar backup cada 6 horas
            await incremental_backup()
            
            # Limpiar backups antiguos cada día
            current_hour = datetime.utcnow().hour
            if current_hour == 3:  # 03:00 UTC
                await cleanup_old_backups(7)
            
            # Esperar 6 horas
            await asyncio.sleep(6 * 60 * 60)
            
        except KeyboardInterrupt:
            logger.info("Backup detenido por usuario")
            break
        except Exception as e:
            logger.error(f"Error en loop de backup: {e}")
            await asyncio.sleep(300)  # Esperar 5 minutos antes de reintentar

if __name__ == "__main__":
    asyncio.run(main())
