# Investigación y motor avanzado — 15 septiembre 2026

## Objetivo y resultado verificable

El objetivo del usuario es encontrar instancias públicas que tengan 0–1 jugadores cuando llegue a ellas, con un panel independiente para cada juego. La versión avanzada implementa selección por evidencia, reobservación dirigida, exploración progresiva y medición prospectiva. **Todavía no hay evidencia suficiente para afirmar un 70 % de entradas exitosas**, ni para cuantificar una mejora respecto al selector anterior. Las pruebas de software comprueban su lógica, no la ocupación real al entrar.

Un servidor no reaparece necesariamente en cada consulta. Guardar su ID permite buscarlo de nuevo, pero no conocer su población entre observaciones. El motor mantiene referencias; el panel solo mantiene recomendaciones vigentes. Desaparecer de una página nunca significa estar vacío o cerrado.

## Repositorios inspeccionados

Se leyó código fuente mediante la API de GitHub. No se ejecutaron ni incorporaron bibliotecas, código o recursos de estos proyectos. Las referencias están fijadas a commits para distinguir lo realmente revisado de cambios posteriores.

| Proyecto | Hallazgo concreto | Aplicación a SerRob |
|---|---|---|
| [RoValra: serverApi.js](https://github.com/NotValra/RoValra/blob/753e06aa9b02abd8acf832caffc79b30cee3ae59/src/content/core/apis/serverApi.js) | `/v1/servers/details` usa `isRovalraApi`, su backend propio. `first_seen` es evidencia de primera observación. Otros métodos hacen POST a gamejoin. | Mantener historial propio; no confundir su backend con una API pública de Roblox ni primera observación con nacimiento del servidor. |
| [RoValra: filtros de antigüedad](https://github.com/NotValra/RoValra/blob/753e06aa9b02abd8acf832caffc79b30cee3ae59/src/content/core/games/servers/filters/uptimefilters.js) | Las rutas oldest/newest consultan el índice de RoValra. | No prometer localizar los más antiguos globalmente sin un índice y cobertura propios. |
| [RoValra: serverlist.js](https://github.com/NotValra/RoValra/blob/753e06aa9b02abd8acf832caffc79b30cee3ae59/src/content/features/games/serverlist/serverlist.js) | Construye enlaces con PlaceId y gameInstanceId. | Enlace explícito por instancia; mantener advertencia de compatibilidad de Roblox. |
| [BTRoblox: serverdetails.js](https://github.com/AntiBoomz/BTRoblox/blob/13c81b89d15faa4e84df32c13886bfdbb8d31c11/js/feat/serverdetails.js) | Consulta gamejoin con credenciales del navegador para datos de servidor/región; su caché de éxito dura una hora. | Esa caché sirve para ubicación; sería inadecuada para ocupación. Un bot sin sesión no hereda esas capacidades. |
| [robloxserverfinder: aroblox.js](https://github.com/neves768/robloxserverfinder/blob/51232860ac2e4b0e645fb2f57a2e1ca071bea6e9/aroblox.js) | Usa la ruta antigua getgameinstancesjson, índices y numerosas búsquedas concurrentes. | No reutilizar endpoints antiguos ni aumentar concurrencia como solución a límites. |

Smart-Join apareció en resultados de búsqueda con ranking ponderado, pero la consulta actual del repositorio `Frank1o3/Smart-Join` devolvió 404. No se considera código auditado ni evidencia de funcionalidad disponible.

## Límites comprobados y documentación oficial

- Roblox documenta [factores de matchmaking](https://create.roblox.com/docs/matchmaking/scoring). Un clasificador externo no controla la asignación ni reserva plazas.
- Los [deep links con gameInstanceId](https://create.roblox.com/docs/production/promotion/deeplinks) están documentados como heredados/obsoletos. SerRob conserva el enlace exacto; falta validar su apertura efectiva en los dispositivos del usuario.
- Los [límites y respuestas 429](https://create.roblox.com/docs/cloud/reference/rate-limits) requieren moderar solicitudes. El presupuesto propio de SerRob no es una cuota oficial del endpoint público de servidores.
- Roblox anunció [pruebas de cambios en la lista de servidores](https://devforum.roblox.com/t/test-updates-to-server-list-page/3966648). Ni la paginación ni la cobertura completa se pueden asumir permanentes.

### Prueba real: Steal An Egg, Place 107778070777162

Se hicieron GET públicos, sin cookies ni sesión de jugador, con hasta dos páginas por ronda y objetivo de diez segundos entre inicios. Resultado observado:

| Ronda | Páginas | Instancias únicas | Conteos 0–1 | Coincidencias con ronda anterior |
|---|---:|---:|---:|---:|
| 1 | 2 | 200 | 200 | — |
| 2 | 1 | 15 | 15 | 2 |
| 3 | — | — | — | Se detuvo ante HTTP 429 |

Eso confirma que se pueden obtener conteos bajos en esa sesión, pero también que su visibilidad cambia rápidamente. No demuestra que las instancias ausentes se llenaran. El replay de estas dos instantáneas no resolvió ningún resultado del TOP dentro de su ventana posterior: rápido/equilibrado quedaron con diez comprobaciones pendientes y precisión con dos. Es una muestra insuficiente para evaluar el 70 %, no un porcentaje de éxito del 100 %.

## Algoritmo implementado

### 1. Historial acotado y evidencia temporal

Por Place y JobId se conservan hasta 120 muestras de los últimos 30 minutos; para puntuar se usan hasta diez minutos. Una separación de más de 45 segundos corta la secuencia de evidencia. Las lecturas separadas por menos de tres segundos no cuentan como confirmaciones independientes. La caché declarada por el proveedor reduce la frescura. Si el proveedor sirve caché sin declararla, no podemos detectarlo con certeza.

Se calculan cobertura temporal, crecimiento positivo por minuto, pendiente de regresión de las cinco muestras recientes, desviación de ocupación y máximo reciente. Un pico de llenado penaliza el resultado aunque luego vuelva a uno. Confirmación requiere dos observaciones separadas al menos cinco segundos, con las dos últimas bajo el filtro y sin crecimiento detectado. Estabilidad exige al menos cuatro muestras, treinta segundos, baja variación y cobertura suficiente.

### 2. Ranking explicable, sin porcentaje inventado

La puntuación 0–100 combina frescura (30), ocupación (16), duración de baja población ponderada por cobertura (20), cobertura (14) y muestras (20). Resta crecimiento (hasta 25), variación (15), picos sobre el filtro (20) y doce puntos a un cero aislado. Son pesos heurísticos, pendientes de calibrar con datos representativos. La bonificación de continuidad del TOP es de tres puntos y solo se aplica a datos con menos de media vigencia; nunca prolonga su caducidad.

| Perfil | Selección | Vigencia efectiva |
|---|---|---|
| Equilibrado | Prioriza evidencia; permite provisionales identificados | La configurada |
| Precisión | Confirmado y puntuación mínima 60; puede abstenerse | Máximo 10 s |
| Evento | Igual que precisión, con evidencia posterior a activarlo | Máximo 8 s |
| Rápido | Menor población y después dato más reciente | La configurada |

Evento se activa manualmente tras el aviso del otro bot; no lee mensajes de un bot ajeno. Volver a activarlo reinicia el corte temporal. Elegir precisión puede producir paneles vacíos cuando la API no permite reobservación suficiente: el filtro no se amplía silenciosamente.

### 3. Descubrimiento y seguimiento

Cada recorrido consulta la cabecera ascendente. Mantiene hasta 50 referencias de baja población y pistas de sus cursores durante 45 segundos. Fuera de las rondas de exploración, prioriza páginas que podrían contener referencias pendientes. Cada tercera ronda intenta avanzar desde el cursor de exploración guardado. Los cursores son pistas opacas, no localizadores permanentes. Un 400 de cursor invalida la pista y vuelve a la cabecera en el próximo recorrido.

Máximo configurado de páginas y veinte segundos por recorrido. Equilibrado continúa explorando si solo obtuvo cinco provisionales; rápido puede detenerse al completar el TOP. Los resultados ausentes conservan su fecha original y caducan individualmente. Máximo 500 referencias en memoria por Place, retenidas hasta cinco minutos.

Se registra una media móvil del llenado detectado, pero no se usa para acelerar consultas. Se corrigió el comportamiento inicial que reducía el intervalo a la mitad por presión o evento: contradecía el ajuste manual para reducir tráfico. Los botones tampoco acortan el intervalo del panel. Paneles del mismo Place comparten el intervalo mínimo solicitado entre sus suscripciones activas. Cada 429 duplica la separación entre peticiones hasta sesenta segundos, respeta Retry-After y recupera ritmo gradualmente después de respuestas correctas. Si varios juegos compiten por el presupuesto, sus intervalos efectivos aumentan.

### 4. Medición prospectiva

Después de clasificar, se registran comprobaciones por JobId, filtro y perfil, deduplicadas por bloques de treinta segundos. Solo observaciones posteriores, entre diez y treinta segundos desde emitir la comprobación, pueden resolverla. Éxito significa conteo bajo el filtro; fallo significa que lo supera; ausencia de reobservación es desconocido. La primera reobservación válida resuelve la comprobación: no demuestra estabilidad continua durante toda la ventana.

Calidad muestra aciertos, fallos, desconocidos, pendientes y cobertura. Si S son aciertos, F fallos y U desconocidos, el porcentaje conocido es S/(S+F), pero el rango descriptivo incluyendo desconocidos es [S/(S+F+U), (S+U)/(S+F+U)]. Los pendientes no entran aún. El indicador interno de objetivo requiere treinta resultados y límite inferior ≥70 %; **no es un intervalo estadístico de confianza, ni una certificación de entradas reales**. Hay dependencia entre muestras y sesgo de visibilidad.

Los reportes privados después de preparar una entrada permiten indicar 0–1 jugadores adicionales, más de uno o imposibilidad de entrar. Solo puede votar una vez el usuario de esa comprobación, durante cinco minutos, en el mismo Discord. Son voluntarios y se presentan separados; no entrenan ni alteran el ranking. El enlace directo del panel no permite conocer quién hizo clic ni revalidar al pulsarlo.

## Cómo evaluar en el host

```sh
python tools/audit.py capture 107778070777162 --rounds 30 --interval 15 --pages 2 --rpm 20 --output data/audit-egg.jsonl
python tools/audit.py replay data/audit-egg.jsonl
```

La captura termina ante errores, respeta el espaciado y no sobrescribe archivos. Ejecútala con el bot detenido para no sumar dos presupuestos independientes sobre la misma IP. El replay compara rápido, equilibrado y precisión sobre las mismas páginas capturadas, resolviendo predicciones antes de ingerir nuevos datos. Evalúa ranking y abstención; no simula la política de adquisición de páginas ni prueba entradas reales. Las ventanas incompletas al final permanecen pendientes.

Antes de declarar una mejora, comparar periodos tranquilos y eventos del mismo juego, cobertura, resultados resueltos, errores, latencia hasta entrar y reportes reales. Una simulación sintética prueba que el código maneja llenado y ausencias; no sustituye esos datos. No se afirma una mejora numérica todavía.
