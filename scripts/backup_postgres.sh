#!/bin/bash

#############################################
# Techloop Sim - PostgreSQL Backup Script
#############################################

set -e

# Configurazione
BACKUP_DIR="./backups/postgres"
CONTAINER_NAME="techloop-postgres"
DB_NAME="homeassistant"
DB_USER="ha_user"
RETENTION_DAYS=7

# Crea directory backup se non esiste
mkdir -p "$BACKUP_DIR"

# Timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/ha_backup_$TIMESTAMP.sql.gz"

echo "========================================"
echo "Techloop Sim - PostgreSQL Backup"
echo "========================================"
echo "Data: $(date)"
echo "Database: $DB_NAME"
echo "Container: $CONTAINER_NAME"
echo ""

# Verifica che il container sia running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ ERRORE: Container $CONTAINER_NAME non è in esecuzione!"
    exit 1
fi

# Esegui backup
echo "🔄 Avvio backup..."
docker exec -t "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "✅ Backup completato: $BACKUP_FILE ($BACKUP_SIZE)"
else
    echo "❌ ERRORE durante il backup!"
    exit 1
fi

# Pulizia vecchi backup
echo ""
echo "🧹 Pulizia backup più vecchi di $RETENTION_DAYS giorni..."
find "$BACKUP_DIR" -name "ha_backup_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete
REMAINING_BACKUPS=$(find "$BACKUP_DIR" -name "ha_backup_*.sql.gz" -type f | wc -l)
echo "📦 Backup rimanenti: $REMAINING_BACKUPS"

echo ""
echo "========================================"
echo "✅ BACKUP COMPLETATO CON SUCCESSO"
echo "========================================"

# Lista ultimi 5 backup
echo ""
echo "Ultimi 5 backup:"
ls -lht "$BACKUP_DIR"/ha_backup_*.sql.gz | head -5