import asyncio

from src.errores import FalloDependencia
from src.schemas import Advertencia, Dependencias, PrestamoWeb, ResumenOut, SancionesWeb, SocioWeb


async def consultar_sanciones(recursos, rut, request_id, deadline):
    try:
        datos = await recursos.sanciones.consultar(rut, request_id, deadline)
        return SancionesWeb(estado="VERIFICADO", tiene_sancion=datos.tiene_sancion, bloqueado=datos.bloqueado,
            multas_pendientes=datos.multas_pendientes, monto_total_multas=datos.monto_total_multas), None
    except FalloDependencia as exc:
        return SancionesWeb(estado="NO_REGISTRADO" if exc.estado == "NO_ENCONTRADO" else "DESCONOCIDO", motivo=exc.estado), exc


async def consultar_libros(recursos, libros, request_id, deadline):
    # N trabajadores por resumen, no N tareas por préstamo/libro. La admisión del
    # cliente aplica además el límite global de este proceso sobre todos los GET.
    pendientes, resultados = iter(sorted(libros)), {}

    async def trabajador():
        for libro_id in pendientes:
            try:
                resultados[libro_id] = await recursos.catalogo.libro(libro_id, request_id, deadline)
            except FalloDependencia as exc:
                resultados[libro_id] = exc

    async with asyncio.TaskGroup() as grupo:
        for _ in range(min(len(libros), recursos.config.catalogo_por_resumen)):
            grupo.create_task(trabajador())
    return resultados


async def resumen(recursos, socio_id, identidad, request_id, deadline):
    socio = await recursos.prestamos.socio(socio_id, identidad, request_id, deadline)
    relevantes = sorted((p for p in socio.prestamos if p.estado in {"ACTIVO", "EN_REVISION", "DEVOLUCION_PENDIENTE"}), key=lambda p: p.id)
    async with asyncio.TaskGroup() as grupo:
        sanciones_tarea = grupo.create_task(consultar_sanciones(recursos, socio.rut, request_id, deadline))
        libros_tarea = grupo.create_task(consultar_libros(recursos, {p.libro_id for p in relevantes}, request_id, deadline))
    sanciones, fallo_sanciones = sanciones_tarea.result()
    libros, advertencias, listas = libros_tarea.result(), [], {"ACTIVO": [], "EN_REVISION": [], "DEVOLUCION_PENDIENTE": []}
    fallos_libros = []
    for libro_id, valor in sorted(libros.items()):
        if isinstance(valor, FalloDependencia):
            fallos_libros.append(valor)
            advertencias.append(Advertencia(codigo=f"CATALOGO_{valor.estado}", libro_id=libro_id,
                mensaje="No se pudo obtener el título y autor de este libro"))
    if fallo_sanciones:
        advertencias.append(Advertencia(codigo=f"SANCIONES_{fallo_sanciones.estado}",
            mensaje="La verificación de sanciones es desconocida; no confirma habilitación"))
    for prestamo in relevantes:
        libro = libros[prestamo.libro_id]
        listas[prestamo.estado].append(PrestamoWeb(**prestamo.model_dump(),
            **({"titulo": None, "autor": None} if isinstance(libro, FalloDependencia) else libro)))
    return ResumenOut(socio=SocioWeb(id=socio.id, nombre=socio.nombre), prestamos_activos=listas["ACTIVO"],
        prestamos_en_revision=listas["EN_REVISION"], prestamos_pendientes=listas["DEVOLUCION_PENDIENTE"],
        sanciones=sanciones, parcial=bool(advertencias), advertencias=advertencias, request_id=request_id,
        dependencias=Dependencias(prestamos="DISPONIBLE", catalogo=fallos_libros[0].estado if fallos_libros else (
            "DISPONIBLE" if libros else "NO_CONSULTADO"), sanciones=fallo_sanciones.estado if fallo_sanciones else "DISPONIBLE"))
