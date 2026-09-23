# Experimento ABET 6

## Pregunta experimental

¿Cómo cambia el tamaño en bytes de una representación de `LibroDTO` serializada como JSON UTF-8 y como Protocol Buffers al aumentar la cantidad de ejemplares? ¿Qué efecto tiene aplicar gzip con el mismo nivel a ambos formatos?

## Hipótesis

Estas hipótesis se fijan antes de ejecutar las mediciones y no se modifican en función de los resultados:

- **H1:** Protocol Buffers producirá una representación menor que JSON UTF-8 sin comprimir para el mismo contenido lógico.
- **H2:** La ventaja porcentual disminuirá al aplicar gzip, porque la estructura repetitiva de JSON es altamente compresible.

## Variables

### Independiente

Cantidad de ejemplares del `LibroDTO`: 1, 5, 10, 25, 50, 100, 250 y 500. Los seis primeros cubren desde un elemento hasta un catálogo mediano; 250 y 500 amplían el rango para observar cómo crecen las representaciones con colecciones mayores.

### Dependientes

- Tamaño serializado en bytes de Protobuf y JSON UTF-8.
- Tamaño en bytes de cada representación comprimida con gzip.
- Ahorro porcentual de Protobuf frente a JSON, antes y después de gzip.

### Controladas

- Mismos datos lógicos y mismos campos en ambas representaciones.
- Mismo libro, valores de strings y codificación UTF-8.
- Mismo patrón determinista de estados (`DISPONIBLE` y `PRESTADO`) y mismos IDs.
- Mismo total de ejemplares e igual conteo de disponibles.
- Mismo esquema Protocol Buffers definido por `catalogo.proto`.
- JSON compacto, sin espacios de formato: `ensure_ascii=False` y separadores `(',', ':')`; así se mide el formato de intercambio y no whitespace de presentación.
- Mismo nivel gzip (9) y `mtime=0` para que la compresión sea reproducible.
- Mismo entorno Python y mismas versiones de dependencias dentro de cada ejecución.
- Los datos se construyen una vez por tamaño y se serializan repetidamente sin consultar bases de datos ni servicios.

## Entorno experimental

- macOS 14.8, arquitectura `arm64`.
- Python 3.13.7.
- Protobuf 7.36.2; los stubs incluidos requieren runtime Protobuf 7.35.1 o posterior.
- Matplotlib 3.11.2.
- zlib 1.2.12, usado por gzip en este entorno.

La versión de Python y Protobuf también se registra en cada fila de `resultados.csv`. El experimento se ejecuta localmente desde el repositorio; no requiere Docker, red, bases de datos ni servicios levantados.

## Método

Para cada cantidad de ejemplares, el script construye un `catalogo_pb2.LibroDTO` y un diccionario JSON equivalente con `id`, `titulo`, `autor`, `genero`, `total_ejemplares`, `ejemplares_disponibles` y la lista de ejemplares (`id`, `libro_id`, `estado`). Antes de medir, convierte el mensaje Protobuf de vuelta a un diccionario y comprueba igualdad exacta con el objeto JSON.

Protobuf se serializa mediante `SerializeToString()`. JSON se serializa con `json.dumps(objeto, ensure_ascii=False, separators=(',', ':')).encode('utf-8')`. JSON compacto evita que indentación o espacios artificiales favorezcan una representación. Ambos bytes se comprimen con `gzip.compress` al nivel 9 y `mtime=0`; el nivel único permite comparar ambos formatos bajo la misma configuración. Se mide la longitud de los bytes resultantes, incluidos los encabezados y trailer propios de gzip.

El ahorro sin comprimir se calcula como `(json_bytes - protobuf_bytes) / json_bytes * 100`. El ahorro comprimido se calcula como `(json_gzip_bytes - protobuf_gzip_bytes) / json_gzip_bytes * 100`.

El gráfico usa eje X logarítmico para distinguir legiblemente los tamaños espaciados de 1 a 500 ejemplares; el eje Y permanece lineal y muestra bytes.

## Repeticiones

Se realizan 30 serializaciones por tamaño. Las repeticiones comprueban estabilidad y reproducibilidad del procedimiento; no se usan para inventar variación. Si las 30 longitudes son iguales, la desviación estándar reportada es 0.

## Ejecución

Desde la raíz del repositorio:

```bash
python -m pip install -r docs/experimentos/requirements.txt
python docs/experimentos/experimento.py
```

El script escribe los resultados en su propia carpeta y sobrescribe de forma explícita los CSV y el PNG de una ejecución anterior.

## Archivos generados

- `experimento.py`: construcción de datos, serialización, compresión, cálculos y gráfico.
- `requirements.txt`: dependencias locales del experimento (`protobuf` para importar los stubs existentes y `matplotlib` para el PNG).
- `resultados.csv`: una fila por tamaño y repetición, con bytes, porcentajes y metadatos de versión.
- `resultados_resumen.csv`: medias y desviaciones estándar poblacionales por tamaño.
- `resultados.png`: comparación gráfica de las cuatro series.

## Resultados

La corrida generó 240 mediciones (ocho tamaños por 30 repeticiones). Las desviaciones estándar poblacionales de las cuatro métricas de tamaño (JSON y Protobuf, comprimidas y sin comprimir) fueron 0 bytes para cada cantidad de ejemplares: las serializaciones repetidas dieron las mismas longitudes y no se añadió variación artificial.

| Ejemplares | Protobuf (B) | JSON UTF-8 (B) | Protobuf + gzip (B) | JSON + gzip (B) | Ahorro Protobuf | Ahorro Protobuf + gzip |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 116 | 242 | 135 | 212 | 52.1% | 36.3% |
| 5 | 194 | 440 | 163 | 245 | 55.9% | 33.5% |
| 10 | 292 | 689 | 179 | 262 | 57.6% | 31.7% |
| 25 | 584 | 1,432 | 230 | 307 | 59.2% | 25.1% |
| 50 | 1,072 | 2,670 | 304 | 373 | 59.9% | 18.5% |
| 100 | 2,046 | 5,145 | 444 | 505 | 60.2% | 12.1% |
| 250 | 4,974 | 12,572 | 785 | 904 | 60.4% | 13.2% |
| 500 | 9,848 | 24,946 | 1,357 | 1,545 | 60.5% | 12.2% |

La columna de ahorro usa las fórmulas definidas en el método y los valores sin redondear del CSV; la tabla muestra porcentajes redondeados a una decimal.

## Interpretación

- **H1 se confirma para los datos medidos:** Protobuf es menor sin comprimir en los ocho tamaños. El ahorro observado varía entre 52.1% y 60.5%; crece de 52.1% en N=1 a 60.5% en N=500.
- **H2 se confirma para los datos medidos:** para cada N, el ahorro porcentual de Protobuf + gzip es menor que el ahorro de Protobuf sin comprimir. El ahorro comprimido queda entre 12.1% y 36.3%, frente a 52.1%–60.5% sin compresión.
- La ventaja tras gzip no decrece de forma estrictamente monótona: sube de 12.1% en N=100 a 13.2% en N=250 y vuelve a 12.2% en N=500. La conclusión de H2 es una comparación por tamaño, no una afirmación de monotonicidad.
- Gzip reduce notablemente JSON por su repetición de nombres de campo. En N=500, JSON pasa de 24,946 a 1,545 bytes y Protobuf de 9,848 a 1,357 bytes. En N=1, Protobuf + gzip ocupa 135 bytes, más que los 116 bytes de Protobuf sin comprimir: en un payload tan pequeño, el overhead gzip supera el ahorro.
- En todos los tamaños Protobuf + gzip sigue siendo menor que JSON + gzip, aunque la distancia porcentual se reduce.

## Limitaciones

- Los bytes serializados no equivalen a latencia.
- No se incluyen headers HTTP.
- No se incluye el framing HTTP/2 de gRPC.
- No se mide CPU del servidor ni tiempo total de request.
- La estructura de datos y los valores (especialmente strings y repetición de claves) influyen en los tamaños.
- Gzip puede cambiar la ventaja relativa entre formatos.
- Se mide la representación del mensaje, no un intercambio gRPC completo ni una API REST completa.
- Solo se usa un libro, strings fijos y un patrón fijo de estados; otros contenidos y esquemas pueden dar resultados distintos.
- Gzip usa nivel 9, que puede tener costos de CPU no medidos.
- El experimento no demuestra que gRPC siempre sea más rápido: no mide latencia ni throughput y solo compara tamaños de estas representaciones y datos.

## Conclusiones

Para el `LibroDTO` y los contenidos concretos de esta corrida, Protobuf ocupó menos bytes que JSON UTF-8 tanto antes como después de gzip. La ventaja sin comprimir se mantuvo amplia; gzip redujo especialmente el tamaño de JSON y disminuyó la ventaja porcentual de Protobuf. Los datos respaldan H1 y H2 dentro del alcance medido. No permiten concluir que Protobuf/gRPC sea más rápido en general: esa afirmación requeriría medir latencia, CPU, framing, transporte y cargas de trabajo representativas.
