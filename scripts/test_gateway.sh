#!/usr/bin/env bash

# Configuración de modo estricto para depuración
set -o pipefail

# Ruta base de la API gateway
BASE="http://localhost:8080"
# Directorio del reporte
DIR_REPORTE="scripts"
# Ruta del archivo
REPORTE="$DIR_REPORTE/resultados_gateway.txt"

# Garantizar que el directorio exista
mkdir -p "$DIR_REPORTE"

echo "Pruebas del Gateway - $(date '+%Y-%m-%d %H:%M:%S')" > "$REPORTE"
echo "" >> "$REPORTE"

# Devuelve solo el código HTTP (000 si no hay conexión)
codigo() { 
  curl -s -o /dev/null -w "%{http_code}" "$@"
}

verificar() {  # $1: nombre, $2: esperado, $3: obtenido
  local estado="OK   "
  [ "$2" = "$3" ] || estado="FALLO"
  local linea="[$estado] $1 (esperado $2, obtenido $3)"
  echo "$linea" | tee -a "$REPORTE"
}

login() {  # $1: usuario, $2: clave -> imprime respuesta JSON completa
  curl -s -X POST -H "Content-Type: application/json" \
       -d "{\"username\":\"$1\",\"password\":\"$2\"}" "$BASE/auth/login"
}

codigo_login() {
  codigo -X POST -H "Content-Type: application/json" \
         -d "{\"username\":\"$1\",\"password\":\"$2\"}" "$BASE/auth/login"
}

# --- 1. Pruebas de Autenticación y Autorización ---
verificar "Health sin token"             200 "$(codigo "$BASE/health")"
verificar "Sin token"                    401 "$(codigo "$BASE/api/v1/socios")"
verificar "Token falso"                  401 "$(codigo -H "Authorization: Bearer abc.def.ghi" "$BASE/api/v1/socios")"
verificar "Login con clave incorrecta"   401 "$(codigo_login admin mala)"
verificar "Login correcto"               200 "$(codigo_login admin admin123)"

# Extracción de Token JWT (Parseo seguro)
RESPUESTA_LOGIN=$(login admin admin123)
T=$(echo "$RESPUESTA_LOGIN" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

if [ -z "$T" ]; then
  echo "[ERROR CRÍTICO] No se pudo obtener el access_token. Abortando pruebas." | tee -a "$REPORTE"
  exit 1
fi

verificar "Con token valido"             200 "$(codigo -H "Authorization: Bearer $T" "$BASE/api/v1/socios")"
verificar "X-API-Key directa al gateway" 401 "$(codigo -H "X-API-Key: biblioteca-2026-clave-secreta" "$BASE/api/v1/socios")"
verificar "Recurso inexistente"          404 "$(codigo -H "Authorization: Bearer $T" "$BASE/api/v1/xyz")"

# --- 2. Aislamiento de red (000 = no hay conexion) ---
verificar "Prestamos no expuesto (:8000)" 000 "$(codigo -m 3 http://localhost:8000/v1/socios)"
verificar "Sanciones no expuesto (:8001)" 000 "$(codigo -m 3 http://localhost:8001/)"
verificar "Catalogo no expuesto (:50051)" 000 "$(codigo -m 3 http://localhost:50051/)"

# --- 3. Rate limiting (Control de concurrencia y ventana) ---
echo "Esperando 61 s para vaciar la ventana de rate limiting..."
sleep 61

n200=0
n429=0
for i in {1..25}; do
  c=$(codigo -H "Authorization: Bearer $T" "$BASE/api/v1/socios")
  [ "$c" = "200" ] && n200=$((n200 + 1))
  [ "$c" = "429" ] && n429=$((n429 + 1))
done

verificar "Rate limit: respuestas 200 de 25" 20 "$n200"
verificar "Rate limit: respuestas 429 de 25" 5  "$n429"

echo ""
echo "Reporte guardado en $REPORTE"