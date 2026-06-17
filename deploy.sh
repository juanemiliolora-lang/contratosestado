#!/bin/bash
# deploy.sh — Sube cambios a GitHub y dispara deploy en Render automáticamente
# Uso: ./deploy.sh "mensaje del cambio"
# Ej:  ./deploy.sh "actualizo keywords CIIU 4651"

set -e

MSG=${1:-"actualizacion dashboard seace $(date '+%d/%m/%Y %H:%M')"}

echo ""
echo "📦 Preparando archivos..."
git add .

# Verificar si hay algo que commitear
if git diff --cached --quiet; then
  echo "⚠  Sin cambios nuevos para subir."
  exit 0
fi

echo "💾 Guardando: $MSG"
git commit -m "$MSG"

echo "🚀 Subiendo a GitHub..."
git push origin main

echo ""
echo "✅ Listo. Render detecta el cambio y hace deploy automático."
echo "   Revisa el estado en: https://dashboard.render.com"
echo ""
