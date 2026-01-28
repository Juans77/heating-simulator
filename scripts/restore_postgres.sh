#!/bin/bash

#############################################
# Techloop Sim - PostgreSQL Restore Script
#############################################

set -e

# Configurazione
BACKUP_DIR="./backups/postgres"
CONTAINER_NAME="techloop-postgres"
DB_NAME="homeassistant"
DB_USER="ha_user"

echo "========================================"
echo "Techloop Sim - PostgreSQL Restore"
echo "========================================"
echo ""

# Verifica argomento
if [ -z "$1" ]; then
    echo "❌ ERRORE: Specifica il file di backup da ripristinare"
    echo ""
    echo "Uso: $0 <backup_file>"
    echo ""
    echo "Backup disponibili:"
    ls -lht "$BACKUP_DIR"/ha_backup_*.sql.gz 2>/dev/null || echo "  Nessun backup trovato in $BACKUP_DIR"
    exit 1
fi

BACKUP_FILE="$1"

# Verifica che il file esista
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ ERRORE: File $BACKUP_FILE non trovato!"
    exit 1
fi

# Verifica container
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ ERRORE: Container $CONTAINER_NAME non è in esecuzione!"
    exit 1
fi

# Conferma
echo "⚠️  ATTENZIONE: Stai per ripristinare il database da:"
echo "   $BACKUP_FILE"
echo ""
echo "   Questo sovrascriverà TUTTI i dati attuali del database!"
echo ""
read -p "Sei sicuro? (digita 'yes' per confermare): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "❌ Operazione annullata"
    exit 0
fi

echo ""
echo "🔄 Fermo Home Assistant..."
docker stop techloop-homeassistant 2>/dev/null || true

echo "🗑️  Elimino database esistente..."
docker exec -t "$CONTAINER_NAME" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"

echo "🔨 Ricreo database vuoto..."
docker exec -t "$CONTAINER_NAME" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

echo "📥 Ripristino backup..."
gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME"

if [ $? -eq 0 ]; then
    echo "✅ Ripristino completato!"
else
    echo "❌ ERRORE durante il ripristino!"
    exit 1
fi

echo ""
echo "🔄 Riavvio Home Assistant..."
docker start techloop-homeassistant

echo ""
echo "========================================"
echo "✅ RIPRISTINO COMPLETATO CON SUCCESSO"
echo "========================================"
echo ""
echo "Attendi qualche secondo che HA si riconnetta al database..."