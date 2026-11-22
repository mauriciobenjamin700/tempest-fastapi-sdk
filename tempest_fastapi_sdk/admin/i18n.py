"""Interface catalog of the admin panel, in English and Brazilian Portuguese.

Every piece of text the admin itself puts on screen — template labels,
buttons, empty states, flash messages and the errors the router renders —
is looked up here by a stable key. Pick the language with
``AdminSite(locale="pt-BR")``; the default ``"en"`` renders exactly the
strings the panel shipped before the catalog existed.

Templates call ``t("key", name=value)`` (``tn("key", count)`` for a count,
which reads ``key.one`` or ``key.other``); the router and the form helpers
call :func:`admin_text`. Text that comes from the consumer — model and field
names, ``verbose_name``, action labels, ``AdminSite(title=...)`` — is shown
as given.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

AdminLocale = Literal["en", "pt-BR"]
"""A language the admin catalog ships."""

ADMIN_LOCALES: Final[tuple[AdminLocale, ...]] = ("en", "pt-BR")
"""Every locale in :data:`ADMIN_MESSAGES`, the default first."""

DEFAULT_ADMIN_LOCALE: Final[AdminLocale] = "en"
"""Locale of an ``AdminSite`` built without ``locale=``."""

ADMIN_MESSAGES: Final[Mapping[str, Mapping[str, str]]] = {
    "en": {
        "action.rows_selected.one": "{count} row(s) selected.",
        "action.rows_selected.other": "{count} row(s) selected.",
        "autocomplete.empty": "No matches",
        "card.level.green": "OK",
        "card.level.red": "Critical",
        "card.level.yellow": "Attention",
        "card.vs_before": "vs {previous} before",
        "common.delete": "Delete",
        "common.done": "Done",
        "common.download": "Download",
        "common.edit": "Edit",
        "common.filter": "Filter",
        "common.next": "Next",
        "common.no": "No",
        "common.page_of": "Page {page} of {pages}",
        "common.pagination": "Pagination",
        "common.prev": "Prev",
        "common.search": "Search",
        "common.search_placeholder": "Search…",
        "common.view": "View",
        "common.yes": "Yes",
        "dashboard.browse": "Browse",
        "dashboard.business_metrics": "Business metrics",
        "dashboard.cpu": "CPU",
        "dashboard.disk": "Disk",
        "dashboard.empty.lead": "No models registered. Register an",
        "dashboard.empty.middle": "on the",
        "dashboard.empty.tail": "to populate this dashboard.",
        "dashboard.memory": "Memory",
        "dashboard.memory_usage": "{used} / {total} GB",
        "dashboard.new": "+ New",
        "dashboard.system_metrics": "System metrics",
        "detail.audit": "Audit",
        "detail.created_at": "Created at",
        "detail.created_by": "Created by",
        "detail.delete_confirm": "Delete this record? This cannot be undone.",
        "detail.history": "History",
        "detail.history.after": "after",
        "detail.history.before": "before",
        "detail.history.create": "create",
        "detail.history.delete": "delete",
        "detail.history.field": "field",
        "detail.history.update": "update",
        "detail.history_empty": "No recorded changes for this record.",
        "detail.inline_add": "Add",
        "detail.inline_editing_first": "Editing the first {shown} of {total}.",
        "detail.inline_empty": "No related records.",
        "detail.inline_error": "Some inline rows could not be saved.",
        "detail.inline_save": "Save {label}",
        "detail.inline_showing_first": "Showing the first {shown} of {total}.",
        "detail.updated_at": "Updated at",
        "detail.updated_by": "Updated by",
        "error.edit_conflict": (
            "Someone else saved this record after you opened it. Nothing was saved:"
            " your values are below — review them and save again to overwrite."
        ),
        "error.out_of_scope": (
            "This record would fall outside the rows you may manage; nothing was saved."
        ),
        "filter.shortcut.last_30_days": "30 days",
        "filter.shortcut.last_7_days": "7 days",
        "filter.shortcut.last_month": "Last month",
        "filter.shortcut.this_month": "This month",
        "filter.shortcut.today": "Today",
        "flash.already_submitted": (
            "This form was already submitted; it did not run again."
        ),
        "form.back": "Back",
        "form.cancel": "Cancel",
        "form.create": "Create",
        "form.error.choice": "Choose one of the listed values for {label}.",
        "form.error.fk_missing": "Select a valid choice; that record does not exist.",
        "form.error.invalid": "Invalid value for {label}.",
        "form.error.json": "Invalid JSON for {label}.",
        "form.error.required": "This field is required.",
        "form.error.upload_type": "Only {types} files are accepted.",
        "form.file_current": "Current: {value} — choose a file to replace.",
        "form.json_hint": "JSON — must parse (e.g.",
        "form.none": "— none —",
        "form.password_keep": "Leave blank to keep the current password.",
        "form.required": "required",
        "form.save": "Save",
        "form.timezone_hint": "Timezone: {zone} — stored as UTC.",
        "form.title.create": "New {model}",
        "form.title.edit": "Edit {model}",
        "import.back": "Back to list",
        "import.column.errors": "Errors",
        "import.column.row": "Row",
        "import.confirm.one": "Confirm import of {count} record",
        "import.confirm.other": "Confirm import of {count} records",
        "import.confirm_blocked": (
            "Nothing is imported while any row has an error: fix the file and preview"
            " it again."
        ),
        "import.created.one": "Created {count} record(s).",
        "import.created.other": "Created {count} record(s).",
        "import.error.conflict": (
            "This row conflicts with a stored record (a duplicate value or a missing"
            " reference)."
        ),
        "import.error.not_committed": (
            "Nothing was imported: some rows were refused when the import ran. The"
            " table changed since the preview; review the errors below."
        ),
        "import.error.payload": (
            "This preview expired or belongs to another import. Upload the file again."
        ),
        "import.error.too_large": (
            "The file is larger than {max_kib} KiB. Split it and import each part."
        ),
        "import.error.unreadable": "Could not read the file as UTF-8 CSV.",
        "import.expected_columns": "Expected header columns:",
        "import.file": "CSV file",
        "import.header_rule": (
            "The first row must be the header; each following row becomes one record."
            " Up to {max_kib} KiB."
        ),
        "import.nothing_to_import": "The file has no rows to import.",
        "import.preview.accepted.one": "{count} of {total} row(s) will be imported.",
        "import.preview.accepted.other": "{count} of {total} row(s) will be imported.",
        "import.preview.refused.one": "{count} row refused:",
        "import.preview.refused.other": "{count} rows refused:",
        "import.preview.sample.one": "Preview of the first row",
        "import.preview.sample.other": "Preview of the first {count} rows",
        "import.preview_again": "Preview another file",
        "import.preview_submit": "Preview",
        "import.title": "Import {models}",
        "list.actions": "Actions",
        "list.activate": "Activate",
        "list.any": "— any —",
        "list.apply": "Apply",
        "list.apply_selected": "Apply to selected",
        "list.bulk_action": "Bulk action",
        "list.bulk_confirm": "Apply the selected action to the checked rows?",
        "list.bulk_confirm_dangerous": (
            'Run "{label}" on the checked rows? This cannot be undone.'
        ),
        "list.date_from": "{label} from",
        "list.date_to": "{label} to",
        "list.deactivate": "Deactivate",
        "list.empty": "No records.",
        "list.export_csv": "Export CSV",
        "list.export_json": "Export JSON",
        "list.import_csv": "Import CSV",
        "list.lens_all": "All",
        "list.lenses": "Lenses",
        "list.new": "New {model}",
        "list.records.one": "{count} record",
        "list.records.other": "{count} records",
        "list.select_all": "Select all",
        "list.select_row": "Select row",
        "login.email": "Email",
        "login.error.csrf": "The sign-in form expired, please try again",
        "login.error.throttled": "Too many failed logins, try again later",
        "login.password": "Password",
        "login.sign_in": "Sign in",
        "logs.col.level": "Level",
        "logs.col.logger": "Logger",
        "logs.col.message": "Message",
        "logs.col.timestamp": "Timestamp",
        "logs.empty_filter": "No records match the current filter.",
        "logs.export_json": "Export .json",
        "logs.export_md": "Export .md",
        "logs.export_note": (
            "An export covers every record matching the current filter, newest first,"
            " up to {max} — not just this page. Markdown wraps each traceback in a"
            " fenced block so it survives a paste into an issue; JSON ships the"
            " records verbatim."
        ),
        "logs.heading": "Application logs",
        "logs.no_files.lead": "No log files found. File logging is written by",
        "logs.no_files.tail": "; once it runs, records show up here.",
        "logs.page_of_total": "Page {page} of {pages} ({total} records)",
        "logs.read_at": "Read at",
        "logs.refresh": "Refresh",
        "logs.refresh_title": "Reload the newest records with the current filter",
        "logs.search": "Search message",
        "logs.search_placeholder": "substring…",
        "logs.source": "Source",
        "logs.source_all": "all",
        "logs.traceback": "Traceback",
        "map.cities": "Cities",
        "map.city": "City",
        "map.empty": "No {value_label} with a location yet.",
        "map.failed": "This map could not be counted. The error is in the server log.",
        "map.legend_city": "city (area = {value_label})",
        "map.legend_title": "{value_label} per state",
        "map.located": "With location",
        "map.located_detail": "{states} state(s), {cities} city(ies) shown",
        "map.state": "State",
        "map.states": "States",
        "map.total": "Total",
        "map.unlocated": "No location",
        "metrics.failed": (
            "These indicators could not be collected. The error is in the server log."
        ),
        "mfa.code": "Authentication code",
        "mfa.error.invalid": "Invalid code",
        "mfa.error.throttled": "Too many invalid codes, try again later",
        "mfa.heading": "Two-factor authentication",
        "mfa.lead": "Enter the 6-digit code from your authenticator app.",
        "mfa.title": "Two-factor",
        "mfa.verify": "Verify",
        "nav.dashboard": "Dashboard",
        "nav.label": "Admin navigation",
        "nav.logout": "Logout",
        "nav.logs": "Logs",
        "nav.maps": "Maps",
        "nav.metrics": "Metrics",
        "nav.models": "Models",
        "nav.sql": "SQL console",
        "nav.storage": "Storage",
        "nav.system": "System",
        "nav.tasks": "Tasks",
        "nav.toggle": "Toggle navigation",
        "nav.view_site": "View site",
        "secrets.copy.one": "Copy this value now: it is shown only on this page.",
        "secrets.copy.other": (
            "Copy these values now: they are shown only on this page."
        ),
        "secrets.reload_runs": "Reloading runs the action again.",
        "secrets.reload_safe.one": "Reloading will not show it again.",
        "secrets.reload_safe.other": "Reloading will not show them again.",
        "site.index_subtitle": "Site administration",
        "sql.affected.one": "Statement executed. {count} row affected.",
        "sql.affected.other": "Statement executed. {count} rows affected.",
        "sql.allowed": "Allowed:",
        "sql.denied": "Denied:",
        "sql.empty": "The query returned no rows.",
        "sql.error.denied": "Refused by policy",
        "sql.error.failed": "Statement failed",
        "sql.nothing": "nothing",
        "sql.null": "NULL",
        "sql.placeholder": "SELECT * FROM ...",
        "sql.row_cap": "Row cap:",
        "sql.rows.one": "{count} row",
        "sql.rows.other": "{count} rows",
        "sql.run": "Run",
        "sql.seconds": "{value}s",
        "sql.statement": "Statement",
        "sql.tables": "Tables:",
        "sql.truncated": "truncated at {max} rows",
        "sql.warning": (
            "This console can modify data. Statements run against the live database"
            " and are not undoable from here."
        ),
        "storage.back": "Back to storage",
        "storage.back_root": "Back to the root",
        "storage.bytes": "{count} bytes",
        "storage.col.modified": "Modified",
        "storage.col.name": "Name",
        "storage.col.size": "Size",
        "storage.col.type": "Type",
        "storage.content": "Content",
        "storage.delete_confirm": "Delete this file? This cannot be undone.",
        "storage.empty": "The bucket is empty.",
        "storage.fact.etag": "ETag",
        "storage.fact.key": "Key",
        "storage.file": "File",
        "storage.flash.deleted": "Deleted {key}.",
        "storage.flash.moved": "Moved {key} to {target}.",
        "storage.flash.replaced": "Replaced {key}.",
        "storage.flash.saved": "Saved {key}.",
        "storage.flash.uploaded": "Uploaded {key}.",
        "storage.folder": "Folder",
        "storage.folder_kind": "folder",
        "storage.items.one": "{count} item",
        "storage.items.other": "{count} items",
        "storage.name_placeholder": "keep the file's name",
        "storage.no_preview.lead": "No preview for",
        "storage.no_preview.tail": ". Download it to open.",
        "storage.not_found": "Object not found",
        "storage.nothing_under": "Nothing under",
        "storage.object": "Object",
        "storage.open": "Open",
        "storage.overwrite": "Overwrite",
        "storage.read_only": "Read-only",
        "storage.rename": "Rename",
        "storage.rename_to": "Rename or move to",
        "storage.replace": "Replace",
        "storage.replace_content": "Replace content",
        "storage.save_as": "Save as",
        "storage.save_as_folders": "creates folders",
        "storage.save_as_optional": "optional;",
        "storage.save_hint": (
            "Saving is refused if the file changed since this page loaded."
        ),
        "storage.too_large": (
            "Too large to preview here (over {size}). Download it to read."
        ),
        "storage.upload": "Upload",
        "storage.upload_limit": "Up to {size} per file, into",
        "tasks.all": "All",
        "tasks.attempts_of": "{attempts} of {max}",
        "tasks.back": "Back to tasks",
        "tasks.cancel": "Cancel this run",
        "tasks.cancel_hint": (
            "Cancelling is cooperative: the row flips now, and the worker stops at"
            " its next progress check."
        ),
        "tasks.col.attempts": "Attempts",
        "tasks.col.finished": "Finished",
        "tasks.col.kind": "Kind",
        "tasks.col.progress": "Progress",
        "tasks.col.requested": "Requested",
        "tasks.col.result": "Result",
        "tasks.col.retries": "Retries",
        "tasks.col.stage": "Stage",
        "tasks.col.started": "Started",
        "tasks.col.status": "Status",
        "tasks.col.task": "Task",
        "tasks.default": "default",
        "tasks.error": "Error",
        "tasks.every": "every {seconds}s",
        "tasks.no_runs.lead": "No runs recorded yet. Rows appear here once a",
        "tasks.no_runs.tail": "enqueues work.",
        "tasks.no_schedule": "No tasks registered on the broker.",
        "tasks.off": "off",
        "tasks.on_demand": "on demand",
        "tasks.once_at": "once at {at}",
        "tasks.percent": "{value} percent",
        "tasks.run": "Run",
        "tasks.run_gone": (
            "This run is no longer stored. Retention sweeps delete finished rows, so"
            " a bookmarked link outlives the record it points at."
        ),
        "tasks.run_not_found": "Run not found",
        "tasks.runs": "Runs",
        "tasks.schedule": "Schedule",
        "tasks.scope": (
            "This panel shows what is declared on the broker and what was persisted"
            " by the workers. It does not show live queue depth — TaskIQ exposes none."
        ),
        "tasks.up_to": "up to {max}",
    },
    "pt-BR": {
        "action.rows_selected.one": "{count} linha selecionada.",
        "action.rows_selected.other": "{count} linhas selecionadas.",
        "autocomplete.empty": "Nenhum resultado",
        "card.level.green": "OK",
        "card.level.red": "Crítico",
        "card.level.yellow": "Atenção",
        "card.vs_before": "contra {previous} antes",
        "common.delete": "Excluir",
        "common.done": "Concluir",
        "common.download": "Baixar",
        "common.edit": "Editar",
        "common.filter": "Filtrar",
        "common.next": "Próxima",
        "common.no": "Não",
        "common.page_of": "Página {page} de {pages}",
        "common.pagination": "Paginação",
        "common.prev": "Anterior",
        "common.search": "Buscar",
        "common.search_placeholder": "Buscar…",
        "common.view": "Ver",
        "common.yes": "Sim",
        "dashboard.browse": "Abrir",
        "dashboard.business_metrics": "Indicadores do negócio",
        "dashboard.cpu": "CPU",
        "dashboard.disk": "Disco",
        "dashboard.empty.lead": "Nenhum modelo registrado. Registre um",
        "dashboard.empty.middle": "no",
        "dashboard.empty.tail": "para preencher este painel.",
        "dashboard.memory": "Memória",
        "dashboard.memory_usage": "{used} / {total} GB",
        "dashboard.new": "+ Novo",
        "dashboard.system_metrics": "Métricas do sistema",
        "detail.audit": "Auditoria",
        "detail.created_at": "Criado em",
        "detail.created_by": "Criado por",
        "detail.delete_confirm": "Excluir este registro? Isso não pode ser desfeito.",
        "detail.history": "Histórico",
        "detail.history.after": "depois",
        "detail.history.before": "antes",
        "detail.history.create": "criação",
        "detail.history.delete": "exclusão",
        "detail.history.field": "campo",
        "detail.history.update": "alteração",
        "detail.history_empty": "Nenhuma alteração registrada para este registro.",
        "detail.inline_add": "Adicionar",
        "detail.inline_editing_first": "Editando os primeiros {shown} de {total}.",
        "detail.inline_empty": "Nenhum registro relacionado.",
        "detail.inline_error": "Algumas linhas não puderam ser salvas.",
        "detail.inline_save": "Salvar {label}",
        "detail.inline_showing_first": "Mostrando os primeiros {shown} de {total}.",
        "detail.updated_at": "Atualizado em",
        "detail.updated_by": "Atualizado por",
        "error.edit_conflict": (
            "Outra pessoa salvou este registro depois que você o abriu. Nada foi"
            " salvo: seus valores estão abaixo — revise e salve de novo para"
            " sobrescrever."
        ),
        "error.out_of_scope": (
            "Este registro ficaria fora das linhas que você pode gerenciar; nada foi"
            " salvo."
        ),
        "filter.shortcut.last_30_days": "30 dias",
        "filter.shortcut.last_7_days": "7 dias",
        "filter.shortcut.last_month": "Mês passado",
        "filter.shortcut.this_month": "Este mês",
        "filter.shortcut.today": "Hoje",
        "flash.already_submitted": (
            "Este formulário já foi enviado; ele não rodou de novo."
        ),
        "form.back": "Voltar",
        "form.cancel": "Cancelar",
        "form.create": "Criar",
        "form.error.choice": "Escolha um dos valores listados para {label}.",
        "form.error.fk_missing": "Escolha uma opção válida; esse registro não existe.",
        "form.error.invalid": "Valor inválido para {label}.",
        "form.error.json": "JSON inválido para {label}.",
        "form.error.required": "Este campo é obrigatório.",
        "form.error.upload_type": "Só são aceitos arquivos {types}.",
        "form.file_current": "Atual: {value} — escolha um arquivo para substituir.",
        "form.json_hint": "JSON — precisa ser válido (ex.:",
        "form.none": "— nenhum —",
        "form.password_keep": "Deixe em branco para manter a senha atual.",
        "form.required": "obrigatório",
        "form.save": "Salvar",
        "form.timezone_hint": "Fuso horário: {zone} — gravado em UTC.",
        "form.title.create": "Novo: {model}",
        "form.title.edit": "Editar: {model}",
        "import.back": "Voltar para a lista",
        "import.column.errors": "Erros",
        "import.column.row": "Linha",
        "import.confirm.one": "Confirmar a importação de {count} registro",
        "import.confirm.other": "Confirmar a importação de {count} registros",
        "import.confirm_blocked": (
            "Nada é importado enquanto alguma linha tiver erro: corrija o arquivo e"
            " gere a prévia de novo."
        ),
        "import.created.one": "{count} registro criado.",
        "import.created.other": "{count} registros criados.",
        "import.error.conflict": (
            "Esta linha conflita com um registro gravado (valor duplicado ou"
            " referência inexistente)."
        ),
        "import.error.not_committed": (
            "Nada foi importado: algumas linhas foram recusadas na hora da"
            " importação. A tabela mudou desde a prévia; veja os erros abaixo."
        ),
        "import.error.payload": (
            "Esta prévia expirou ou pertence a outra importação. Envie o arquivo de"
            " novo."
        ),
        "import.error.too_large": (
            "O arquivo passa de {max_kib} KiB. Divida-o e importe cada parte."
        ),
        "import.error.unreadable": "Não foi possível ler o arquivo como CSV em UTF-8.",
        "import.expected_columns": "Colunas esperadas no cabeçalho:",
        "import.file": "Arquivo CSV",
        "import.header_rule": (
            "A primeira linha precisa ser o cabeçalho; cada linha seguinte vira um"
            " registro. Até {max_kib} KiB."
        ),
        "import.nothing_to_import": "O arquivo não tem linhas para importar.",
        "import.preview.accepted.one": "{count} de {total} linha(s) será importada.",
        "import.preview.accepted.other": "{count} de {total} linhas serão importadas.",
        "import.preview.refused.one": "{count} linha recusada:",
        "import.preview.refused.other": "{count} linhas recusadas:",
        "import.preview.sample.one": "Prévia da primeira linha",
        "import.preview.sample.other": "Prévia das primeiras {count} linhas",
        "import.preview_again": "Gerar prévia de outro arquivo",
        "import.preview_submit": "Gerar prévia",
        "import.title": "Importar {models}",
        "list.actions": "Ações",
        "list.activate": "Ativar",
        "list.any": "— qualquer —",
        "list.apply": "Aplicar",
        "list.apply_selected": "Aplicar aos selecionados",
        "list.bulk_action": "Ação em lote",
        "list.bulk_confirm": "Aplicar a ação escolhida às linhas marcadas?",
        "list.bulk_confirm_dangerous": (
            'Executar "{label}" nas linhas marcadas? Isso não pode ser desfeito.'
        ),
        "list.date_from": "{label} desde",
        "list.date_to": "{label} até",
        "list.deactivate": "Desativar",
        "list.empty": "Nenhum registro.",
        "list.export_csv": "Exportar CSV",
        "list.export_json": "Exportar JSON",
        "list.import_csv": "Importar CSV",
        "list.lens_all": "Todos",
        "list.lenses": "Visões",
        "list.new": "Novo: {model}",
        "list.records.one": "{count} registro",
        "list.records.other": "{count} registros",
        "list.select_all": "Selecionar todos",
        "list.select_row": "Selecionar linha",
        "login.email": "E-mail",
        "login.error.csrf": "O formulário de login expirou; tente de novo",
        "login.error.throttled": (
            "Muitas tentativas de login falharam; tente mais tarde"
        ),
        "login.password": "Senha",
        "login.sign_in": "Entrar",
        "logs.col.level": "Nível",
        "logs.col.logger": "Logger",
        "logs.col.message": "Mensagem",
        "logs.col.timestamp": "Data e hora",
        "logs.empty_filter": "Nenhum registro atende ao filtro atual.",
        "logs.export_json": "Exportar .json",
        "logs.export_md": "Exportar .md",
        "logs.export_note": (
            "A exportação cobre todo registro que atende ao filtro atual, do mais"
            " novo para o mais antigo, até {max} — não só esta página. O Markdown põe"
            " cada traceback num bloco cercado, para sobreviver a uma colagem numa"
            " issue; o JSON leva os registros como estão."
        ),
        "logs.heading": "Logs da aplicação",
        "logs.no_files.lead": (
            "Nenhum arquivo de log encontrado. O log em arquivo é escrito por"
        ),
        "logs.no_files.tail": "; quando ele rodar, os registros aparecem aqui.",
        "logs.page_of_total": "Página {page} de {pages} ({total} registros)",
        "logs.read_at": "Lido em",
        "logs.refresh": "Atualizar",
        "logs.refresh_title": "Recarregar os registros mais novos com o filtro atual",
        "logs.search": "Buscar na mensagem",
        "logs.search_placeholder": "trecho…",
        "logs.source": "Origem",
        "logs.source_all": "todos",
        "logs.traceback": "Traceback",
        "map.cities": "Cidades",
        "map.city": "Cidade",
        "map.empty": "Nenhum(a) {value_label} com localização ainda.",
        "map.failed": (
            "Não foi possível contar este mapa. O erro está no log do servidor."
        ),
        "map.legend_city": "cidade (área = {value_label})",
        "map.legend_title": "{value_label} por estado",
        "map.located": "Com localização",
        "map.located_detail": "{states} estado(s), {cities} cidade(s) no mapa",
        "map.state": "Estado",
        "map.states": "Estados",
        "map.total": "Total",
        "map.unlocated": "Sem localização",
        "metrics.failed": (
            "Não foi possível coletar estes indicadores. O erro está no log do"
            " servidor."
        ),
        "mfa.code": "Código de autenticação",
        "mfa.error.invalid": "Código inválido",
        "mfa.error.throttled": "Muitos códigos inválidos; tente mais tarde",
        "mfa.heading": "Autenticação em dois fatores",
        "mfa.lead": "Digite o código de 6 dígitos do seu app autenticador.",
        "mfa.title": "Dois fatores",
        "mfa.verify": "Verificar",
        "nav.dashboard": "Painel",
        "nav.label": "Navegação do admin",
        "nav.logout": "Sair",
        "nav.logs": "Logs",
        "nav.maps": "Mapas",
        "nav.metrics": "Indicadores",
        "nav.models": "Modelos",
        "nav.sql": "Console SQL",
        "nav.storage": "Arquivos",
        "nav.system": "Sistema",
        "nav.tasks": "Tarefas",
        "nav.toggle": "Abrir ou fechar a navegação",
        "nav.view_site": "Ver site",
        "secrets.copy.one": "Copie este valor agora: ele só aparece nesta página.",
        "secrets.copy.other": (
            "Copie estes valores agora: eles só aparecem nesta página."
        ),
        "secrets.reload_runs": "Recarregar executa a ação de novo.",
        "secrets.reload_safe.one": "Recarregar não o mostra de novo.",
        "secrets.reload_safe.other": "Recarregar não os mostra de novo.",
        "site.index_subtitle": "Administração do site",
        "sql.affected.one": "Comando executado. {count} linha afetada.",
        "sql.affected.other": "Comando executado. {count} linhas afetadas.",
        "sql.allowed": "Permitido:",
        "sql.denied": "Negadas:",
        "sql.empty": "A consulta não devolveu linhas.",
        "sql.error.denied": "Recusado pela política",
        "sql.error.failed": "O comando falhou",
        "sql.nothing": "nada",
        "sql.null": "NULL",
        "sql.placeholder": "SELECT * FROM ...",
        "sql.row_cap": "Limite de linhas:",
        "sql.rows.one": "{count} linha",
        "sql.rows.other": "{count} linhas",
        "sql.run": "Executar",
        "sql.seconds": "{value}s",
        "sql.statement": "Comando",
        "sql.tables": "Tabelas:",
        "sql.truncated": "cortado em {max} linhas",
        "sql.warning": (
            "Este console pode alterar dados. Os comandos rodam no banco de produção"
            " e não podem ser desfeitos daqui."
        ),
        "storage.back": "Voltar para os arquivos",
        "storage.back_root": "Voltar para a raiz",
        "storage.bytes": "{count} bytes",
        "storage.col.modified": "Modificado",
        "storage.col.name": "Nome",
        "storage.col.size": "Tamanho",
        "storage.col.type": "Tipo",
        "storage.content": "Conteúdo",
        "storage.delete_confirm": "Excluir este arquivo? Isso não pode ser desfeito.",
        "storage.empty": "O bucket está vazio.",
        "storage.fact.etag": "ETag",
        "storage.fact.key": "Chave",
        "storage.file": "Arquivo",
        "storage.flash.deleted": "{key} excluído.",
        "storage.flash.moved": "{key} movido para {target}.",
        "storage.flash.replaced": "{key} substituído.",
        "storage.flash.saved": "{key} salvo.",
        "storage.flash.uploaded": "{key} enviado.",
        "storage.folder": "Pasta",
        "storage.folder_kind": "pasta",
        "storage.items.one": "{count} item",
        "storage.items.other": "{count} itens",
        "storage.name_placeholder": "manter o nome do arquivo",
        "storage.no_preview.lead": "Sem prévia para",
        "storage.no_preview.tail": ". Baixe para abrir.",
        "storage.not_found": "Objeto não encontrado",
        "storage.nothing_under": "Nada em",
        "storage.object": "Objeto",
        "storage.open": "Abrir",
        "storage.overwrite": "Sobrescrever",
        "storage.read_only": "Somente leitura",
        "storage.rename": "Renomear",
        "storage.rename_to": "Renomear ou mover para",
        "storage.replace": "Substituir",
        "storage.replace_content": "Substituir o conteúdo",
        "storage.save_as": "Salvar como",
        "storage.save_as_folders": "cria pastas",
        "storage.save_as_optional": "opcional;",
        "storage.save_hint": (
            "O salvamento é recusado se o arquivo mudou desde que esta página abriu."
        ),
        "storage.too_large": (
            "Grande demais para a prévia aqui (mais de {size}). Baixe para ler."
        ),
        "storage.upload": "Enviar",
        "storage.upload_limit": "Até {size} por arquivo, em",
        "tasks.all": "Todos",
        "tasks.attempts_of": "{attempts} de {max}",
        "tasks.back": "Voltar para as tarefas",
        "tasks.cancel": "Cancelar esta execução",
        "tasks.cancel_hint": (
            "O cancelamento é cooperativo: a linha muda agora, e o worker para na"
            " próxima checagem de progresso."
        ),
        "tasks.col.attempts": "Tentativas",
        "tasks.col.finished": "Terminou",
        "tasks.col.kind": "Tipo",
        "tasks.col.progress": "Progresso",
        "tasks.col.requested": "Pedida em",
        "tasks.col.result": "Resultado",
        "tasks.col.retries": "Novas tentativas",
        "tasks.col.stage": "Etapa",
        "tasks.col.started": "Começou",
        "tasks.col.status": "Status",
        "tasks.col.task": "Tarefa",
        "tasks.default": "o padrão",
        "tasks.error": "Erro",
        "tasks.every": "a cada {seconds}s",
        "tasks.no_runs.lead": (
            "Nenhuma execução registrada ainda. As linhas aparecem quando um"
        ),
        "tasks.no_runs.tail": "enfileira trabalho.",
        "tasks.no_schedule": "Nenhuma tarefa registrada no broker.",
        "tasks.off": "desligadas",
        "tasks.on_demand": "sob demanda",
        "tasks.once_at": "uma vez em {at}",
        "tasks.percent": "{value} por cento",
        "tasks.run": "Execução",
        "tasks.run_gone": (
            "Esta execução não está mais gravada. A limpeza por retenção apaga as"
            " linhas terminadas, então um link salvo dura mais que o registro."
        ),
        "tasks.run_not_found": "Execução não encontrada",
        "tasks.runs": "Execuções",
        "tasks.schedule": "Agenda",
        "tasks.scope": (
            "Este painel mostra o que está declarado no broker e o que os workers"
            " gravaram. Ele não mostra a profundidade da fila ao vivo — o TaskIQ não"
            " expõe isso."
        ),
        "tasks.up_to": "até {max}",
    },
}
"""``{locale: {key: template}}``; templates use ``str.format`` placeholders."""


def admin_text(locale: str, key: str, /, **params: Any) -> str:
    """Return the catalog text for ``key`` in ``locale``.

    A key missing from ``locale`` falls back to English, and one missing
    from both to the key itself, so a gap renders something readable
    instead of failing the page; ``tests/test_admin_i18n_guard.py`` keeps
    both languages complete, so neither fallback is reached by the SDK's
    own keys.

    Args:
        locale (str): One of :data:`ADMIN_LOCALES`.
        key (str): The catalog key, such as ``"list.apply"``.
        **params (Any): Values for the template's ``{placeholders}``.

    Returns:
        str: The formatted text.
    """
    template = ADMIN_MESSAGES.get(locale, {}).get(key)
    if template is None:
        template = ADMIN_MESSAGES[DEFAULT_ADMIN_LOCALE].get(key, key)
    return template.format(**params) if params else template


def admin_plural(locale: str, key: str, count: int, /, **params: Any) -> str:
    """Return the singular or plural catalog text for ``count``.

    Reads ``<key>.one`` when ``count`` is 1 and ``<key>.other`` otherwise —
    the rule both shipped languages follow — and passes ``count`` to the
    template along with ``params``.

    Args:
        locale (str): One of :data:`ADMIN_LOCALES`.
        key (str): The catalog key without the ``.one`` / ``.other`` suffix.
        count (int): The number the text describes.
        **params (Any): Further ``{placeholder}`` values.

    Returns:
        str: The formatted text.
    """
    suffix = "one" if count == 1 else "other"
    return admin_text(locale, f"{key}.{suffix}", count=count, **params)


__all__: list[str] = [
    "ADMIN_LOCALES",
    "ADMIN_MESSAGES",
    "DEFAULT_ADMIN_LOCALE",
    "AdminLocale",
    "admin_plural",
    "admin_text",
]
