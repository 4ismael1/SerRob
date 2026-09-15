from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import replace
from uuid import uuid4

import discord
from discord import app_commands

from .config import Settings
from .engine import Engine
from .models import Candidate, Panel, join_url, parse_place
from .roblox import ProviderError, Roblox
from .storage import Store
from .analytics import evidence

log = logging.getLogger(__name__)


def safe(text: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:200]


def panel_embed(panel: Panel, engine: Engine, join_mode: str = "legacy", results: list[Candidate] | None = None) -> discord.Embed:
    results = engine.results(panel) if results is None else results
    state = engine.states.get(panel.place_id)
    status = {"active": "Escaneo activo", "paused": "Pausado", "broken": "Requiere reparación"}.get(panel.state, panel.state)
    embed = discord.Embed(title=f"Servidores · {safe(panel.name)}"[:256], color=0x5865F2)
    embed.description = (
        f"**{status}** · Objetivo: **0–{panel.max_players} jugadores**\n"
        f"Perfil: **{panel.profile}** · Consulta objetivo: {panel.interval} s · Vigencia: {panel.ttl} s\n"
        f"[Abrir juego](https://www.roblox.com/games/{panel.place_id}) · Place `{panel.place_id}`\n\n"
        "**Últimos conteos observados** — pueden cambiar antes de entrar.\n"
        + ("Cada resultado tiene su enlace directo. **Buscar mejor ahora** pide una comprobación reciente.\n"
         "Los enlaces directos no revalidan al pulsar y usan el mecanismo heredado de Roblox."
         if join_mode == "legacy" else "Selecciona una instancia para comprobarla. Modo de entrada: abrir el juego.")
    )
    if panel.state == "active":
        for index, candidate in enumerate(results, 1):
            stats = evidence(candidate, panel, time.time())
            label = f"**{stats.band.capitalize()}** · Calidad {stats.score:.0f}/100 (no es probabilidad)"
            link = f"\n[Entrar a esta instancia]({join_url(panel.place_id, candidate.job_id)})" if join_mode == "legacy" else ""
            embed.add_field(
                name=f"{index}. {candidate.playing}/{candidate.capacity} jugadores",
                value=(f"Consultado <t:{int(candidate.observed_at)}:R>\n"
                       f"Dato caduca a las <t:{int(candidate.observed_at + panel.ttl)}:T>\n"
                       f"{label}\n{stats.samples} muestras recientes · Tendencia +{stats.growth_per_minute:g}/min\n"
                       f"Primera observación <t:{int(candidate.first_seen)}:R>\nID: `{candidate.job_id}`{link}"), inline=False)
        if not results:
            embed.add_field(name="Sin candidatos recientes",
                            value="No hay resultados que cumplan el filtro y la vigencia. La búsqueda continúa dentro del presupuesto.", inline=False)
            if state:
                if state.error:
                    cause = "Consulta interrumpida: revisa el error del proveedor indicado abajo."
                elif any(c.eligible(panel, time.time()) for c in state.candidates.values()):
                    cause = "Hay conteos bajos recientes, pero no reúnen la confirmación exigida por este perfil."
                elif state.candidates:
                    cause = "Los conteos anteriores caducaron o superaron el filtro. No se conoce su ocupación actual."
                else:
                    cause = "Aún no se han obtenido candidatos que cumplan el filtro."
                embed.add_field(name="Motivo de la espera", value=cause, inline=False)
    if state and panel.state == "active":
        details = (f"{state.pages} páginas · {state.scanned} instancias · {state.reobserved} ya conocidas.\n"
                   f"Fase: {state.search_phase} · Mayor profundidad recorrida: {state.max_depth}\n"
                   + (f"Grupo en seguimiento: {len(state.focus_jobs)} instancias · Revisiones sin coincidencias: {state.focus_misses}\n" if panel.profile == "profundo" else "")
                   + f"Watchlist: {len(state.watchlist)} · Baja población: {state.low_count}\n{state.reason}.")
        if state.last_success:
            details += f"\nÚltima respuesta correcta: <t:{int(state.last_success)}:R>."
        if state.error:
            details += f"\n⚠ {state.error}\nPróximo intento no antes de <t:{int(state.next_at)}:T>."
            embed.color = 0xF0B232
        embed.add_field(name="Estado de la búsqueda", value=details[:1024], inline=False)
        metrics = state.metrics.get(panel.id)
        if metrics:
            embed.add_field(name="Reobservación a 10–30 s · últimas 24 h",
                            value=(f"Siguen bajo el filtro: **{metrics['success']}** · Lo superan: **{metrics['failed']}**\n"
                                   f"Sin reobservación: **{metrics['unknown']}** · Pendientes: {metrics['pending']}\n"
                                   "Mide datos de la API, no entradas reales. Usa Calidad para ver los reportes."), inline=False)
    if panel.error:
        embed.add_field(name="Aviso del panel", value=panel.error[:1024], inline=False)
    embed.set_footer(text=f"Panel {panel.id} · Lista parcial · Los intervalos dependen de Roblox y la carga")
    return embed


class PanelView(discord.ui.View):
    def __init__(self, bot: ServerBot, panel: Panel, results: list[Candidate]):
        super().__init__(timeout=None)
        self.bot, self.panel_id = bot, panel.id
        options = [discord.SelectOption(label=f"{c.playing}/{c.capacity} · {c.job_id[:8]}", value=c.job_id,
                                        description="Comprobar esta instancia y preparar entrada") for c in results]
        picker = discord.ui.Select(custom_id=f"panel:{panel.id}:select", placeholder="Selecciona un servidor",
                                   options=options or [discord.SelectOption(label="Sin candidatos", value="none")],
                                   disabled=not results or panel.state != "active", row=0)
        picker.callback = self.pick
        self.add_item(picker)
        for action, label, style in (
            ("best", "Buscar mejor ahora", discord.ButtonStyle.success),
            ("refresh", "Actualizar", discord.ButtonStyle.primary),
            ("configure", "Configurar", discord.ButtonStyle.secondary),
            ("toggle", "Reanudar" if panel.state == "paused" else "Pausar", discord.ButtonStyle.secondary),
            ("quality", "Calidad", discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(custom_id=f"panel:{panel.id}:{action}", label=label, style=style, row=1)
            button.callback = getattr(self, action)
            self.add_item(button)
        if bot.settings.join_mode == "legacy" and panel.state == "active":
            for index, candidate in enumerate(results[:5], 1):
                self.add_item(discord.ui.Button(label=f"Entrar {index} · {candidate.job_id[:6]}",
                                               url=join_url(panel.place_id, candidate.job_id), row=2))

    async def quality(self, interaction: discord.Interaction):
        panel = await self.get_panel(interaction)
        if not panel:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self.bot.store.quality(panel.place_id, panel.max_players, panel.profile)
        reports = await self.bot.store.reports(panel.id)
        text = (f"**Calidad · {safe(panel.name)} · últimas 24 h**\n"
                f"Recomendaciones reobservadas bajo el filtro: {stats['success']}\n"
                f"Reobservadas por encima: {stats['failed']}\n"
                f"Sin dato posterior: {stats['unknown']} · Pendientes: {stats['pending']}\n")
        if stats['total']:
            text += f"Cobertura de reobservación: {stats['coverage']:.0%}. Éxito posible entre {stats['lower']:.0%} y {stats['upper']:.0%} considerando los desconocidos.\n"
        text += (f"\n**Reportes voluntarios de entrada**\n"
                 f"Había 0–1 jugadores más: {reports.get('low', 0)}\n"
                 f"Había más: {reports.get('busy', 0)} · No pudo entrar: {reports.get('unavailable', 0)}\n\n"
                 "El 70 % es una meta, no una garantía. Las reobservaciones no verifican el cliente Roblox y los reportes voluntarios pueden tener sesgo.\n"
                 "Perfiles: rapido = ocupación; equilibrado = historial; precision = confirmación; evento = evidencia nueva; profundo = exploración y estabilidad de 60 s.")
        await interaction.followup.send(text[:1950], ephemeral=True)

    async def get_panel(self, interaction: discord.Interaction, manager: bool = False) -> Panel | None:
        panel = await self.bot.store.get(self.panel_id)
        if (not panel or str(interaction.guild_id) != panel.guild_id or str(interaction.channel_id) != panel.channel_id
                or not interaction.message or str(interaction.message.id) != panel.message_id):
            await interaction.response.send_message("Este panel fue eliminado, movido o reemplazado. Usa /panel listar.", ephemeral=True)
            return None
        if manager and not interaction.permissions.manage_guild:
            await interaction.response.send_message("Necesitas el permiso Gestionar servidor para cambiar el panel.", ephemeral=True)
            return None
        return panel

    async def pick(self, interaction: discord.Interaction):
        panel = await self.get_panel(interaction)
        if not panel:
            return
        if not self.bot.allow_action(interaction.user.id):
            await interaction.response.send_message("Espera unos segundos antes de otra comprobación.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        job = (interaction.data or {}).get("values", [""])[0]
        candidate = await self.bot.engine.prepare(panel, job)
        if candidate is None:
            await interaction.followup.send("No pude comprobar esta instancia en los últimos 5 segundos, o ya no cumple el filtro. Usa Buscar mejor ahora para elegir una alternativa recién observada.", ephemeral=True)
            return
        await self.deliver(interaction, panel, candidate)

    async def best(self, interaction: discord.Interaction):
        panel = await self.get_panel(interaction)
        if not panel:
            return
        if not self.bot.allow_action(interaction.user.id):
            await interaction.response.send_message("Espera unos segundos antes de otra comprobación.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        candidate = await self.bot.engine.prepare_best(panel)
        if candidate is None:
            await interaction.followup.send("No encontré una opción que cumpla el filtro con un dato de los últimos 5 segundos. Revisa el estado del panel; la búsqueda respeta el presupuesto disponible.", ephemeral=True)
            return
        await self.deliver(interaction, panel, candidate)

    async def deliver(self, interaction: discord.Interaction, panel: Panel, candidate: Candidate):
        # No rank-based selection: this URL always refers to the selected JobId.
        # Recheck immediately before queueing the Discord send, after any async work.
        current = await self.bot.store.get(panel.id)
        candidate = self.bot.engine.entry_candidate(current, candidate.job_id) if current else None
        if candidate is None:
            await interaction.followup.send("El dato o la configuración cambió antes de preparar la respuesta. Vuelve a buscar.", ephemeral=True)
            return
        receipt = await self.bot.store.receipt(current, candidate, interaction.user.id)
        current = await self.bot.store.get(panel.id)
        candidate = self.bot.engine.entry_candidate(current, candidate.job_id) if current else None
        if candidate is None:
            await interaction.followup.send("La comprobación caducó mientras se preparaba. Vuelve a buscar.", ephemeral=True)
            return
        view = FeedbackView(self.bot, receipt, interaction.user.id)
        if self.bot.settings.join_mode == "legacy":
            view.add_item(discord.ui.Button(label="Intentar entrar · experimental", url=join_url(panel.place_id, candidate.job_id)))
            note = "Enlace heredado de Roblox: puede no funcionar en tu dispositivo. No reserva plaza ni garantiza la instancia."
        else:
            view.add_item(discord.ui.Button(label="Abrir juego", url=f"https://www.roblox.com/games/{panel.place_id}"))
            note = "El modo actual abre el juego; no selecciona esta instancia."
        message = await interaction.followup.send(
            f"**Último conteo: {candidate.playing}/{candidate.capacity} jugadores**\n"
            f"Consultado <t:{int(candidate.observed_at)}:R>. Puede cambiar inmediatamente.\n"
            f"Place: `{panel.place_id}`\nJobId: `{candidate.job_id}`\n{note}",
            view=view, ephemeral=True, wait=True)
        view.message = message
        self.bot.track_task(self.bot.expire_link(message, candidate.observed_at + 10, view))

    async def refresh(self, interaction: discord.Interaction):
        panel = await self.get_panel(interaction)
        if not panel:
            return
        if not self.bot.allow_action(interaction.user.id):
            await interaction.response.send_message("Espera unos segundos antes de otra solicitud.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.engine.refresh(panel)
        await interaction.followup.send("Actualización solicitada. Se comparte con las solicitudes pendientes y respeta los límites de Roblox." if panel.state == "active" else "El panel está pausado.", ephemeral=True)

    async def configure(self, interaction: discord.Interaction):
        panel = await self.get_panel(interaction, manager=True)
        if panel:
            await interaction.response.send_modal(ConfigModal(self.bot, panel))

    async def toggle(self, interaction: discord.Interaction):
        panel = await self.get_panel(interaction, manager=True)
        if not panel:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.change(panel.id, interaction.guild_id, state="paused" if panel.state == "active" else "active")
            await interaction.followup.send("Panel actualizado.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        log.error("Error de componente: %s", type(error).__name__)
        text = "No se pudo completar la acción. Consulta /estado o vuelve a intentarlo."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class FeedbackView(discord.ui.View):
    def __init__(self, bot: ServerBot, receipt: str, user_id: int):
        super().__init__(timeout=300)
        self.bot, self.receipt, self.user_id = bot, receipt, user_id
        self.message = None
        for outcome, label in (("low", "Había 0–1 jugadores más"), ("busy", "Había más de 1"), ("unavailable", "No pude entrar")):
            button = discord.ui.Button(label=label, custom_id=f"report:{receipt}:{outcome}", row=1)
            async def callback(interaction: discord.Interaction, value=outcome):
                await interaction.response.defer()
                accepted = await self.bot.store.report(self.receipt, interaction.user.id, interaction.guild_id, value)
                if accepted:
                    for child in list(self.children):
                        if child.url:
                            self.remove_item(child)
                        else:
                            child.disabled = True
                    await interaction.edit_original_response(content="Gracias. Tu reporte de entrada quedó guardado para medir la calidad real.", view=self)
                else:
                    await interaction.followup.send("El reporte ya fue enviado o caducó.", ephemeral=True)
            button.callback = callback
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Esta comprobación pertenece a otro usuario.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=None)


class ConfigModal(discord.ui.Modal, title="Configurar este juego"):
    def __init__(self, bot: ServerBot, panel: Panel):
        super().__init__(timeout=300)
        self.bot, self.panel_id, self.version = bot, panel.id, panel.version
        self.fields = {}
        for key, label, value in (
            ("name", "Nombre del panel", panel.name),
            ("max_players", "Máximo de jugadores (0–10)", panel.max_players),
            ("interval", "Intervalo objetivo en segundos (5–300)", panel.interval),
            ("freshness", "Vigencia del dato en segundos (5–60)", panel.freshness),
            ("pages", "Máximo de páginas por recorrido (1–5)", panel.pages),
        ):
            field = discord.ui.TextInput(label=label, default=str(value), max_length=100 if key == "name" else 3)
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("Necesitas Gestionar servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            values = {k: (str(v).strip() if k == "name" else int(str(v))) for k, v in self.fields.items()}
            if not values["name"]:
                raise ValueError("El nombre no puede estar vacío.")
            await self.bot.change(self.panel_id, interaction.guild_id, expected_version=self.version, **values)
            await interaction.followup.send("Configuración guardada para este juego. El presupuesto global puede alargar el intervalo solicitado.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(f"No se guardaron los cambios: {exc}", ephemeral=True)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class PanelCommands(app_commands.Group, name="panel", description="Un panel configurable por juego"):
    def __init__(self, bot: ServerBot):
        super().__init__()
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.permissions.manage_guild:
            await interaction.response.send_message("Necesitas Gestionar servidor para administrar paneles.", ephemeral=True)
            return False
        return True

    @app_commands.command(description="Crea un panel independiente para un juego")
    @app_commands.describe(juego="PlaceId o enlace https://www.roblox.com/games/ID", canal="Canal donde se publicará")
    async def crear(self, interaction: discord.Interaction, juego: str, canal: discord.TextChannel,
                    max_jugadores: app_commands.Range[int, 0, 10] = 1,
                    intervalo: app_commands.Range[int, 5, 300] = 10,
                    vigencia: app_commands.Range[int, 5, 60] = 15):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            place = parse_place(juego)
            if canal.guild.id != interaction.guild_id:
                raise ValueError("El canal debe pertenecer a este servidor de Discord.")
            permissions = canal.permissions_for(interaction.guild.me)
            if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                raise ValueError("Necesito Ver canal, Enviar mensajes e Insertar enlaces en el canal elegido.")
            async with self.bot.mutations:
                panels = await self.bot.store.panels()
                existing = next((p for p in panels if p.guild_id == str(interaction.guild_id) and p.place_id == place), None)
                if existing:
                    raise ValueError(f"Este juego ya tiene el panel `{existing.id}`. Usa Configurar o /panel reparar.")
                if sum(p.guild_id == str(interaction.guild_id) for p in panels) >= self.bot.settings.max_panels:
                    raise ValueError("Se alcanzó el límite de paneles de este servidor de Discord.")
                self.bot.check_capacity(panels, place)
                panel = Panel(uuid4().hex[:12], str(interaction.guild_id), str(canal.id), place,
                              f"Place {place}", max_players=max_jugadores, interval=intervalo, freshness=vigencia)
                # Persist intent before creating a remote message; unfinished creations can be repaired.
                panel.state = "broken"
                panel.error = "Creación del mensaje pendiente; usa /panel reparar si se interrumpe."
                await self.bot.store.save(panel)
                try:
                    message = await canal.send(embed=panel_embed(panel, self.bot.engine, self.bot.settings.join_mode))
                    panel.message_id, panel.state, panel.error = str(message.id), "active", ""
                    await self.bot.store.save(panel)
                    await message.edit(embed=panel_embed(panel, self.bot.engine, self.bot.settings.join_mode), view=PanelView(self.bot, panel, []))
                except discord.HTTPException:
                    panel.state, panel.error = "broken", "No se pudo publicar el panel; usa /panel reparar."
                    await self.bot.store.save(panel)
                    raise ValueError(panel.error)
            self.bot.track_task(self.bot.resolve_name(panel.id, place))
            await interaction.followup.send(f"Panel creado: {message.jump_url}\nCada juego tiene su propia configuración. El primer escaneo comprobará el acceso a Roblox.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(description="Lista tus paneles, sus IDs y estado")
    async def listar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        panels = [p for p in await self.bot.store.panels() if p.guild_id == str(interaction.guild_id)]
        if not panels:
            await interaction.followup.send("Todavía no hay paneles. Usa /panel crear.", ephemeral=True)
            return
        lines = [f"`{p.id}` · {safe(p.name)} · **{p.state}** · <#{p.channel_id}> · ≤{p.max_players} · {p.interval}s/{p.freshness}s" for p in panels]
        # Keep every panel visible even when the configured limit is above one message.
        for start in range(0, len(lines), 10):
            await interaction.followup.send("\n".join(lines[start:start + 10]), ephemeral=True)

    @app_commands.command(description="Cambia el filtro y tiempos de un panel")
    async def configurar(self, interaction: discord.Interaction, panel: str,
                         max_jugadores: app_commands.Range[int, 0, 10] = 1,
                         intervalo: app_commands.Range[int, 5, 300] = 10,
                         vigencia: app_commands.Range[int, 5, 60] = 15,
                         paginas: app_commands.Range[int, 1, 5] = 2):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.change(panel, interaction.guild_id, max_players=max_jugadores,
                                  interval=intervalo, freshness=vigencia, pages=paginas)
            await interaction.followup.send("Configuración guardada.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(description="Pausa o reanuda un panel")
    async def pausa(self, interaction: discord.Interaction, panel: str, pausado: bool):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.change(panel, interaction.guild_id, state="paused" if pausado else "active")
            await interaction.followup.send("Estado actualizado.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(description="Elige el algoritmo de selección para este juego")
    @app_commands.choices(modo=[
        app_commands.Choice(name="Equilibrado: historial y frescura", value="equilibrado"),
        app_commands.Choice(name="Precisión: solo candidatos reobservados", value="precision"),
        app_commands.Choice(name="Evento: exige evidencia nueva y reduce vigencia", value="evento"),
        app_commands.Choice(name="Rápido: menor ocupación observada", value="rapido"),
        app_commands.Choice(name="Profundo: explorar y exigir estabilidad de 60 s", value="profundo"),
    ])
    async def perfil(self, interaction: discord.Interaction, panel: str, modo: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.change(panel, interaction.guild_id, profile=modo,
                                  event_since=time.time() if modo == "evento" else 0)
            await interaction.followup.send("Perfil actualizado. Precisión, evento y profundo pueden dejar el TOP vacío hasta reunir evidencia suficiente. Profundo exige estabilidad observada durante al menos 60 s; el filtro de jugadores nunca se amplía solo.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @app_commands.command(description="Recupera un panel borrado o sin permisos; permite moverlo")
    async def reparar(self, interaction: discord.Interaction, panel: str, canal: discord.TextChannel):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if canal.guild.id != interaction.guild_id:
                raise ValueError("Canal incorrecto.")
            async with self.bot.mutations:
                async with self.bot.panel_lock(panel):
                    current = await self.bot.require_panel(panel, interaction.guild_id)
                    self.bot.check_capacity(await self.bot.store.panels(), current.place_id)
                    permissions = canal.permissions_for(interaction.guild.me)
                    if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                        raise ValueError("Faltan permisos para publicar en ese canal.")
                    old_channel, old_message = current.channel_id, current.message_id
                    current.channel_id = str(canal.id)
                    current.state, current.error = "active", ""
                    current.version += 1
                    snapshot = self.bot.engine.results(current)
                    message = await canal.send(embed=panel_embed(current, self.bot.engine, self.bot.settings.join_mode, snapshot))
                    current.message_id = str(message.id)
                    await self.bot.store.save(current)
                    await message.edit(view=PanelView(self.bot, current, snapshot))
                    self.bot.last_render.pop(current.id, None)
                    if old_message:
                        with contextlib.suppress(discord.HTTPException):
                            await self.bot.get_partial_messageable(int(old_channel)).get_partial_message(int(old_message)).edit(content="Panel reemplazado.", embed=None, view=None)
            await interaction.followup.send(f"Panel recuperado: {message.jump_url}", ephemeral=True)
        except (ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc) if isinstance(exc, ValueError) else "No pude publicar en el canal. Revisa los permisos.", ephemeral=True)

    @app_commands.command(description="Elimina el panel y detiene su suscripción")
    async def eliminar(self, interaction: discord.Interaction, panel: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self.bot.mutations:
                async with self.bot.panel_lock(panel):
                    current = await self.bot.require_panel(panel, interaction.guild_id)
                    await self.bot.store.delete(panel)
                    self.bot.last_render.pop(panel, None)
                    self.bot.last_edit.pop(panel, None)
                    if current.message_id:
                        with contextlib.suppress(discord.HTTPException):
                            await self.bot.get_partial_messageable(int(current.channel_id)).get_partial_message(int(current.message_id)).delete()
            await interaction.followup.send("Panel eliminado.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


class ServerBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = Store(settings.database)
        self.roblox = Roblox(settings.rpm)
        self.engine = Engine(self.store, self.roblox, settings.max_places)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(PanelCommands(self))
        self.mutations = asyncio.Lock()
        self.locks: dict[str, asyncio.Lock] = {}
        self.last_render: dict[str, dict] = {}
        self.last_edit: dict[str, float] = {}
        self.user_actions: dict[int, float] = {}
        self.background: set[asyncio.Task] = set()
        self.publisher: asyncio.Task | None = None
        self.closing = False

        @self.tree.command(name="estado", description="Estado del bot y escaneos de este Discord")
        @app_commands.guild_only()
        async def status(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            panels = [p for p in await self.store.panels() if p.guild_id == str(interaction.guild_id)]
            text = (f"Paneles de este Discord: {len(panels)}\n"
                    f"Presupuesto Roblox global configurado: {settings.rpm} solicitudes/minuto (no garantizado).\n"
                    f"Techo actual tras ajustes: {60 / self.roblox.gate.spacing:.1f}/min; 429 recibidos: {self.roblox.rate_limits}.\n"
                    f"Modo de entrada: {settings.join_mode}.\n")
            for p in panels[:8]:
                state = self.engine.states.get(p.place_id)
                text += f"\n**{safe(p.name)}**: {p.state}; {len(self.engine.results(p))} candidatos recientes."
                if state and state.error:
                    text += f"\n{state.error}"
            await interaction.followup.send(text[:1900], ephemeral=True)

        @self.tree.error
        async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            # Do not log HTTP request objects or interaction tokens.
            log.error("Error de comando: %s", type(error).__name__)
            text = "No se pudo completar el comando. Revisa los permisos y /estado."
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)

    def panel_lock(self, panel_id: str):
        return self.locks.setdefault(panel_id, asyncio.Lock())

    def check_capacity(self, panels: list[Panel], place: str):
        active = {p.place_id for p in panels if p.state == "active"}
        if place not in active and len(active) >= self.settings.max_places:
            raise ValueError("Se alcanzó MAX_ACTIVE_PLACES. Pausa otro juego o ajusta capacidad y presupuesto en el host.")

    async def require_panel(self, panel_id: str, guild_id: int) -> Panel:
        panel = await self.store.get(panel_id)
        if not panel or panel.guild_id != str(guild_id):
            raise ValueError("Panel no encontrado en este Discord. Usa /panel listar para ver su ID.")
        return panel

    async def change(self, panel_id: str, guild_id: int, expected_version: int | None = None, **values):
        async with self.mutations:
            async with self.panel_lock(panel_id):
                panel = await self.require_panel(panel_id, guild_id)
                if expected_version is not None and panel.version != expected_version:
                    raise ValueError("La configuración cambió mientras editabas. Abre Configurar otra vez.")
                if panel.state == "broken":
                    raise ValueError("Este panel requiere /panel reparar antes de reactivarlo.")
                updated = replace(panel, **values, version=panel.version + 1)
                updated.validate()
                if updated.state == "active":
                    self.check_capacity(await self.store.panels(), updated.place_id)
                await self.store.save(updated)
                state = self.engine.states.get(updated.place_id)
                if state:
                    state.metrics.pop(panel_id, None)
                self.last_render.pop(panel_id, None)
                await self.engine.refresh(updated)

    def allow_action(self, user_id: int) -> bool:
        now = time.monotonic()
        if now - self.user_actions.get(user_id, -10) < 5:
            return False
        self.user_actions = {k: t for k, t in self.user_actions.items() if now - t < 60}
        if len(self.user_actions) >= 5000:
            return False
        self.user_actions[user_id] = now
        return True

    def track_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        def done(finished):
            self.background.discard(finished)
            if not finished.cancelled() and finished.exception():
                log.error("Tarea secundaria falló: %s", type(finished.exception()).__name__)
        task.add_done_callback(done)

    async def expire_link(self, message, expires: float, feedback: FeedbackView | None = None):
        await asyncio.sleep(max(0, expires - time.time()))
        with contextlib.suppress(discord.HTTPException):
            if feedback:
                for child in list(feedback.children):
                    if child.url:
                        feedback.remove_item(child)
                await message.edit(view=feedback)
            else:
                await message.edit(content="La comprobación caducó. Selecciona la instancia nuevamente en el panel.", view=None)

    async def resolve_name(self, panel_id: str, place: str):
        try:
            async with asyncio.timeout(20):
                name = await self.roblox.title(place)
            async with self.mutations:
                async with self.panel_lock(panel_id):
                    panel = await self.store.get(panel_id)
                    if panel and panel.name == f"Place {place}":
                        panel.name = name
                        panel.version += 1
                        await self.store.save(panel)
        except (ProviderError, TimeoutError):
            log.info("Nombre no disponible para Place %s; se conserva su ID", place)

    async def setup_hook(self):
        await self.store.initialize()
        await self.roblox.start()
        for panel in await self.store.panels():
            if panel.message_id:
                self.add_view(PanelView(self, panel, []), message_id=int(panel.message_id))
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.engine.start()
        self.publisher = asyncio.create_task(self.publish_loop(), name="discord-panel-publisher")

    async def on_ready(self):
        log.info("BOT_READY | Conectado como %s | paneles persistentes habilitados", self.user)

    async def publish_loop(self):
        await self.wait_until_ready()
        while True:
            try:
                for panel in await self.store.panels():
                    if panel.state == "broken" or not panel.message_id:
                        continue
                    if time.monotonic() - self.last_edit.get(panel.id, -100) < self.settings.edit_seconds:
                        continue
                    async with self.panel_lock(panel.id):
                        current = await self.store.get(panel.id)
                        if not current or current.state == "broken":
                            continue
                        snapshot = self.engine.results(current)
                        embed = panel_embed(current, self.engine, self.settings.join_mode, snapshot)
                        payload = embed.to_dict()
                        if self.last_render.get(current.id) == payload:
                            continue
                        try:
                            message = self.get_partial_messageable(int(current.channel_id)).get_partial_message(int(current.message_id))
                            await message.edit(embed=embed, view=PanelView(self, current, snapshot))
                            self.last_render[current.id] = payload
                        except (discord.NotFound, discord.Forbidden):
                            current.state, current.error = "broken", "Mensaje/canal no disponible o sin permisos. Usa /panel reparar."
                            current.version += 1
                            await self.store.save(current)
                        except discord.HTTPException:
                            log.warning("Edición aplazada para panel %s", current.id)
                        finally:
                            self.last_edit[current.id] = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error publicando paneles; recuperación automática")
            await asyncio.sleep(1)

    async def close(self):
        if self.closing:
            return
        self.closing = True
        tasks = list(self.background) + ([self.publisher] if self.publisher else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.engine.close()
        await self.roblox.close()
        await self.store.close()
        await super().close()
