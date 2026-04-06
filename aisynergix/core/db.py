# aisynergix/core/db.py
import os
import json
import shutil
import logging
from aisynergix.config.paths import DB_FILE, BACKUP_DIR, TEMP_DIR

logger = logging.getLogger("synergix.db")

class SoberaniaManager:
    """Gestor de persistencia atómica y sincronización con Greenfield."""
    def __init__(self):
        self.save_count = 0
        self.db = self._load()

    def _load(self) -> dict:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"❌ DB corrupta: {e}")
        return {
            "reputation": {}, "memory": {}, "chat": {},
            "global_stats": {"total": 0, "save_count": 0}
        }

    def save(self):
        """Escritura atómica usando archivos temporales."""
        self.save_count += 1
        tmp_path = os.path.join(TEMP_DIR, "db.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.db, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, DB_FILE)
            
            # Backup cada 50 guardados
            if self.save_count % 50 == 0:
                self._backup()
        except Exception as e:
            logger.error(f"❌ Save error: {e}")

    def _backup(self):
        ts = self.save_count
        shutil.copy(DB_FILE, os.path.join(BACKUP_DIR, f"db_{ts}.bak"))

    def get_user(self, uid_str: str) -> dict:
        """Obtiene perfil con valores por defecto."""
        return self.db["reputation"].get(uid_str, {
            "points": 0, "contributions": 0, "rank": "Iniciado", "lang": "es"
        })

    def update_user_points(self, uid_str: str, points: int):
        if uid_str not in self.db["reputation"]:
            self.db["reputation"][uid_str] = self.get_user(uid_str)
        self.db["reputation"][uid_str]["points"] += points
        self._check_rank(uid_str)
        self.save()

    def _check_rank(self, uid_str: str):
        pts = self.db["reputation"][uid_str]["points"]
        ranks = [
            (0, "Iniciado"), (100, "Activo"), (500, "Sincronizado"),
            (1500, "Arquitecto"), (5000, "Mente Colmena"), (15000, "Oráculo")
        ]
        new_rank = "Iniciado"
        for threshold, rank in ranks:
            if pts >= threshold: new_rank = rank
        self.db["reputation"][uid_str]["rank"] = new_rank

soberania = SoberaniaManager()
