# Estrategia para encontrar servidores poco concurridos que duren

## Qué cambia respecto a buscar el primer servidor con una persona

El objetivo es encontrar una instancia accesible que permanezca poco concurrida, no simplemente un registro cuyo campo `playing` sea 1. Son tres preguntas diferentes: ¿tiene poca población?, ¿la mantiene entre observaciones?, ¿puede entrar este usuario? La lista pública solo aporta evidencia parcial para las dos primeras.

El usuario observa que, al bajar muchas páginas manualmente, a veces encuentra servidores que permanecen solos durante horas. Es una pista para cambiar el muestreo, **no una demostración de que una página profunda equivalga a un servidor antiguo o excluido del matchmaking**. No hemos medido la creación de cientos de servidores por segundo en este juego; tampoco se debe confundir visitas acumuladas con usuarios concurrentes.

## Hallazgos en fuentes primarias

1. [Matchmaking de Roblox](https://create.roblox.com/docs/matchmaking): filtra servidores no elegibles y asigna según compatibilidad. La ocupación no es el único criterio. Una población baja no implica que vaya a recibir al siguiente jugador.
2. [Scoring oficial](https://create.roblox.com/docs/matchmaking/scoring): influyen amigos, latencia, idioma y otras señales. El atributo Age de esa documentación es edad de jugadores, **no antigüedad del servidor**. No usamos ese atributo como supuesta fecha de creación.
3. [Server Management, anuncio de marzo de 2026](https://devforum.roblox.com/t/revamped-server-restarts-and-server-management/4537709): un reinicio con demora retira primero las instancias afectadas del matchmaking. Esa es una explicación posible de servidores que dejan de recibir usuarios, pero no confirma el caso de Steal An Egg. Además pueden terminar cerrando. Los datos administrativos requieren permisos sobre la experiencia; no son un índice público accesible a cualquier bot.
4. [Reporte de abril de 2026 sobre reinicios aplazados](https://devforum.roblox.com/t/delayed-server-restart-doesnt-stop-matchmaking-to-existing-servers/4559812): Roblox marcó resuelto el problema reportado el 15 de abril. No tomamos un bug histórico como comportamiento garantizado actual.
5. [Enlaces de instancia](https://create.roblox.com/docs/production/promotion/deeplinks): gameInstanceId identifica el JobId, pero el mecanismo está obsoleto. Encontrar bien el servidor y abrir correctamente ese servidor son problemas distintos. Existe un [reporte sobre gameInstanceId ignorado](https://devforum.roblox.com/t/deep-link-ignores-gameinstanceid-argument/3815549); no se ha confirmado que explique el error del usuario.

## Repositorios adicionales revisados

Se leyó código sin ejecutarlo ni incorporarlo al proyecto:

- [RoLocate, commit 587fefc](https://github.com/Oqarshi/RoLocate/blob/587fefc94a31db9d77628e2ece62eeee46097365/RoLocate.user.js): su búsqueda emplea paginación y, en un recorrido, selección aleatoria de orden ascendente/descendente; también usa v2 BestLatency. Eso diversifica consultas o busca proximidad, pero no verifica supervivencia en baja población. Cambiar el orden manteniendo un cursor puede invalidar su significado; no copiamos esa técnica.
- [RoServers, commit ffae0ee, servers.js](https://github.com/CalebMulugeta-ui/RoServers/blob/ffae0eebc94ee620f7b27b81bdcdd57402d457a0/roservers/servers.js): recorre páginas con un máximo, usa credenciales del navegador y comenta anomalías con limit=100. Nuestra prueba anterior sí obtuvo páginas de 100, por lo que esa observación del autor no es una regla universal. Su [pool.js](https://github.com/CalebMulugeta-ui/RoServers/blob/ffae0eebc94ee620f7b27b81bdcdd57402d457a0/roservers/pool.js) acota resoluciones y se detiene ante 429. La caché de región no sirve para garantizar ocupación reciente.
- RoValra y BTRoblox se revisaron en la [investigación anterior](INVESTIGACION.md). La antigüedad/indexación de RoValra depende de su backend; BTRoblox consulta detalles desde la sesión del navegador. No se ha encontrado en estos archivos una consulta pública sin autenticación que verifique población y acceso actual de cualquier JobId arbitrario.

## Estrategias consideradas

| Estrategia | Ventaja | Problema | Decisión |
|---|---|---|---|
| Repetir la primera página ascendente | Descubrimiento rápido | Repite una fracción de la lista y favorece candidatos de una sola muestra | Mantener solo como opción rápida |
| Aumentar páginas por consulta | Más cobertura inmediata | Consume cuota; una barrida puede envejecer antes de terminar | Presupuesto acotado |
| Avanzar por la lista entre ciclos | Imita bajar más páginas sin multiplicar solicitudes | Cursores móviles; una página profunda no certifica edad | Implementado en profundo |
| Elegir por antigüedad supuesta | Busca candidatos menos transitorios | La API usada no proporciona edad real ni estado de matchmaking | Rechazado como garantía |
| Exigir baja población reobservada | Detecta estabilidad observada | Más abstención y necesita reencontrar JobIds | Implementado en profundo |
| Mostrar datos antiguos para evitar huecos | Panel aparentemente lleno | Empeora la utilidad durante eventos | Rechazado |
| Validar desde sesión real del navegador | Permite comparar el flujo manual con el del bot | Requiere integración adicional y prueba por dispositivo | Pendiente; no se añaden cookies al host |

## Implementación: perfil profundo

### Descubrimiento independiente

Se conserva un cursor de avance que las visitas de seguimiento no sobrescriben. En una ronda de descubrimiento se continúa desde ese cursor; en las otras se revisitan pistas de páginas con candidatos conocidos, alternando entre hasta tres pistas prioritarias. Si no hay pistas, se sigue explorando. La exploración tiene una ronda cada tres ciclos cuando hay seguimiento disponible.

Con una página por ciclo, una secuencia posible es **página 1 → página 2 → revisar página 2 → página 3**. No vuelve obligatoriamente a página 1 después de cada consulta. Con dos páginas dispone de más trabajo por ciclo, pero sigue respetando el presupuesto global. La profundidad mostrada es un contador del recorrido de cursores, no una posición absoluta y estable en todos los servidores de Roblox.

Las pistas duran como máximo 120 segundos en profundo. Eso permite intentar reutilizarlas, no garantiza su validez; un cursor rechazado se descarta sin dar por muerto ningún servidor. El resto de perfiles conserva pistas de 45 segundos. Cada recorrido sigue limitado a veinte segundos y al número de páginas configurado.

### Selección por estabilidad observada

Profundo exige:

- El filtro actual de jugadores; nunca lo amplía a dos o tres por falta de resultados.
- Al menos sesenta segundos entre la primera y última muestra de la secuencia válida.
- Clasificación estable: cuatro muestras o más, sin huecos mayores de 45 segundos, cobertura suficiente, baja variación y sin pico reciente sobre el filtro.
- Puntuación mínima 60 y dato con un máximo de diez segundos de antigüedad.

Los umbrales son hipótesis de diseño, pendientes de calibrar con observaciones reales del juego. Sesenta segundos de evidencia **no significan sesenta segundos de observación continua**, ni predicen horas de tranquilidad. Un cero también necesita estabilidad; el perfil no lo acepta por una sola lectura.

La comprobación privada de entrada vuelve a leer el estado más reciente del JobId después de las esperas de base de datos y exige como máximo cinco segundos. Un enlace directo público no puede interceptar el clic para hacer esa comprobación. Ninguna de las dos vías reserva plaza.

### Cadencia y límites

Se eliminó la aceleración automática por presión/evento y el acortamiento al pulsar Actualizar. El intervalo configurado se respeta como mínimo para el panel; suscripciones al mismo Place comparten la cadencia más rápida solicitada entre ellas. Los límites del proveedor pueden alargarla.

Si el proveedor tarda más que la vigencia, el resultado correcto puede ser un panel vacío. Esa situación se explica ahora en el panel. La solución no es hacer pasar referencias históricas por servidores disponibles.

## Cómo probar esta estrategia

Activa el perfil en un solo juego inicialmente:

```text
/panel perfil panel:ID modo:profundo
```

Conserva al principio el intervalo que ya has ampliado y el límite de páginas; cambia una variable cada vez. El perfil puede tardar varios minutos en reunir evidencia y puede no producir recomendaciones si Roblox impide reobservar. Observa la fase de búsqueda, profundidad, IDs reobservados, 429 y el motivo del panel vacío.

Para evaluar, necesitamos dos resultados separados:

1. **Acceso:** número de intentos que llegan al JobId esperado sin cola/error y con 0–1 jugadores adicionales. Los reportes actuales son voluntarios y no verifican automáticamente el JobId del cliente.
2. **Permanencia:** población a uno, cinco y diez minutos después de entrar, medida en el cliente. El bot actual no tiene esa telemetría; una reobservación pública no sustituye esa medición.

Comparar cabeza frente a páginas profundas durante periodos equivalentes, separando juego, evento y dispositivo. Registrar también ausencia de recomendaciones y límites. No seleccionar solo los intentos exitosos. Un buen porcentaje entre pocas instancias reobservadas puede ocultar muchos resultados desconocidos.

La prueba automatizada demuestra que el cursor avanza incluso con una sola página y no retrocede por una revisión. Otra demuestra que un candidato con historial corto o caduco no pasa profundo. **No se ha demostrado todavía una mejora de entradas reales ni el objetivo del 70 %.**
