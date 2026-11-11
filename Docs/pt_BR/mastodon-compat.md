# Compatibilidade com a API do Mastodon

*Onde o Babble se condiciona às capacidades do servidor, e por quê.*

Verificado contra o Mastodon **4.7.3** (estável) e `mastodon/mastodon@main`
(4.8.0-alpha.3, `api_versions.mastodon` **12**). A documentação publicada em
docs.joinmastodon.org cobre até a versão 11 da API; páginas individuais anotam
a versão em que um recurso foi acrescentado.

As regras de `docs.joinmastodon.org/api/guidelines/`: prefira
`api_versions.mastodon` de `GET /api/v2/instance` em vez de analisar strings de
versão, trate ids como strings opacas, siga o cabeçalho `Link` para paginar e
respeite o cabeçalho `Deprecation` (RFC 9745, enviado desde o Mastodon 4.4.0).
O Babble faz as quatro.

## Condições de capacidade

| Recurso | Introduzido | Condição | Onde |
|---|---|---|---|
| `api_versions.mastodon` | 4.3.0 | fica em cache por cliente depois da primeira busca | `MastodonClient.instance_info_*` (cache e coalescência, ARCH-09), enviando `api.instance.info_async` |
| Coleções | 4.6.0 (v10) | `>= 10` | `window._update_session_actions` |
| Abas do perfil (`PATCH /api/v1/profile`) | 4.6.0 (v8) | `>= 8` | `profileedit.PROFILE_TABS_VERSION` |
| Texto alternativo do avatar/cabeçalho (`PATCH /profile`) | 4.6.0 (v9) | `>= 9` | `profileedit.PROFILE_ALT_TEXT_VERSION` |
| Texto alternativo do avatar/cabeçalho (`PATCH /accounts/update_credentials`) | 4.6.1 (v11) | não usado; conduzido por `/profile` | `profileedit.collect` |
| apelidos `endorse`/`unendorse` | 4.4.0 (v6, lido de v4.4.0; as rotas não existem em v4.3.8) | `is_supported(ENDORSE_API_VERSION, when_unknown=True)`; um 404 tenta `pin`/`unpin` de novo | `ProfilePage.set_endorsed` |
| Tradução (`POST /statuses/:id/translate`) | 4.0.0 | `configuration.translation.enabled` | `timeline._translation_enabled` |
| Acesso a feeds ao vivo (`live_feeds`) | 4.5.0 | `configuration.timelines_access` | `streaming.public_feed_access` |
| Timeline de links em alta | 4.3.0 | `is_supported(LINK_TIMELINE_API_VERSION)` | `explore.supports_link_timeline` |
| `/state` do Wrapped e Generate | 4.6.0 (v8) | `is_supported(ANNUAL_REPORT_STATE_API_VERSION)`; abaixo disso, o Wrapped mostra a página vazia sem perguntar | `wrapped.WrappedDialog._check_state` |
| Política de notificações, pedidos, `unread_count` | 4.3.0 (v2, lido das tags v4.3.x) | `is_supported(NOTIFICATION_POLICY_API_VERSION, when_unknown=True)`: somente um número menor conhecido dispensa a chamada | `NotificationController.supports_filtering`, o menu da página, `preferences._notifications_page` |
| Política `for_bots` | 4.6.0 (v10, `notification_policy.rb`) | sem condição de versão: a linha e sua chave PATCH existem apenas quando a resposta da política traz `for_bots` | `notifications.NotificationPolicyDialog` |
| Ação de filtro `blur` | 4.4.0 (v5) | `is_supported(FILTER_BLUR_API_VERSION)`; um filtro de desfoque existente o mantém | `filters.available_actions` |
| Política de citação padrão (`source[quote_policy]`) | 4.5.0 (v7) | `is_supported(QUOTES_API_VERSION)` esconde a linha nas Preferências e dispensa o PATCH | `preferences._posting_page`, `sync_posting_defaults` |
| `quoted_status_id`, `quote_approval_policy` em `POST /statuses` | 4.5.0 (v7) | `is_supported(QUOTES_API_VERSION, when_unknown=True)`; omitidos para um número menor conhecido | `compose.BabbleComposeView.send_status` |
| Botão de citar, "Quem pode citar", revogação, aba de citações | 4.5.0 (v7) | a própria publicação: `quote_approval`, o estado `quote` e `quotes_count` precisam estar na publicação | `statusactions`, `statusmenu`, `statusviews` |
| `GET /accounts/:id/endorsements` | 4.4.0 (v6, lido de v4.4.0) | `is_supported(ENDORSEMENTS_API_VERSION)` | `profile.ProfilePage.load_extras` |
| `GET /accounts?id[]=` | 4.3.0 | sem condição: alcançado apenas pelas Coleções, que exigem v10 | `fedicollections` |
| `supported_types[]` em `GET /notifications` | 4.6.0 (v10) | não enviado: o Babble não renderiza nenhuma notificação `fallback` | nenhum |

## Limites que o documento da instância anuncia

Leia pelos acessores de `MastodonClient` a partir de
`instance_info['configuration']`, nunca indexando o dicionário no ponto da
chamada. Um limite não é uma condição de versão: um servidor que não diz nada
recebe um valor padrão, e o servidor ainda responde ao que recusa.

| Limite | Documentação | Acessor | Não declarado | Aplicado em |
|---|---|---|---|---|
| `statuses.max_characters` | [#statuses](https://docs.joinmastodon.org/entities/Instance/#statuses) | `max_characters()` | 500 | contador do compositor, `can_post` |
| `statuses.max_media_attachments` | #statuses | `max_media_attachments()` | 4 | `attachment_limit()` |
| `statuses.characters_reserved_per_url` | #statuses | `characters_reserved_per_url()` | 23 | `counted_length()` |
| `polls.max_options` | [#polls](https://docs.joinmastodon.org/entities/Instance/#polls) | `poll_limits()['max_options']` | 4 | linhas do editor de enquete, restauração de rascunho |
| `polls.max_characters_per_option` | #polls | `poll_limits()` | sem limite | comprimento máximo da opção |
| `polls.min_expiration`, `max_expiration` | #polls | `poll_limits()` | lista fixa | `poll_duration_choices()`; mínimo e máximo quando nada da lista fixa serve |
| `media_attachments.description_limit` | [#media_attachments](https://docs.joinmastodon.org/entities/Instance/#media_attachments) | `media_description_limit()` | 420 (pré-4.4; a 4.4.0 elevou o padrão para 1500) | campo de descrição do anexo no compositor |
| `media_attachments.image_size_limit`, `video_size_limit` | #media_attachments | `media_size_limits()` | nenhum | `attachment_refusal` |
| `accounts.max_*` | [#accounts](https://docs.joinmastodon.org/entities/Instance/#accounts) | `profileedit.account_limits` | valores padrão | editor de perfil, incluindo descrições do avatar e do cabeçalho |
| `GET /notifications/unread_count` `limit` | [#unread-count](https://docs.joinmastodon.org/methods/notifications/#unread-count) | `api.notifications.UNREAD_COUNT_LIMIT` | 100 enviado explicitamente (padrão 100, máximo 1000) | o badge mostra `100+` no limite |

O número da API de uma versão é `api_versions.mastodon` em
`lib/mastodon/version.rb` na sua tag: 2 de v4.3.0 a v4.3.9, 6 na v4.4.0, 7 na
v4.5.0, 10 na v4.6.0, 11 na v4.6.1. A documentação nomeia um número apenas a
partir da 4.4.0, e somente em algumas entradas; onde não nomeia, a condição é o
número da versão que introduziu o recurso.

Uma condição é respondida por `client.is_supported(min_version)`, nunca comparando
`mastodon_api_version()` no ponto da chamada: esse acessor devolve `None`
quando a instância não diz nada, e `None` não se compara. `is_supported` usa
`when_unknown=False` por padrão, então um servidor não comprovado recebe um
não — a parte versionada fica fora da interface em vez de ser pedida e dar 404.
`ProfilePage.set_endorsed` é o único chamador que quer a outra política, e a
declara como `is_supported(ENDORSE_API_VERSION, when_unknown=True)`.
`explore.supports_link_timeline` usa a política padrão `when_unknown=False`.

Dois testes sustentam isso: `version gate call sites` falha numa comparação
escrita contra o acessor, e quando a string `version` legível é *analisada* para
decidir uma condição (como o `explore.py` já fez).

## Pontos de acesso (endpoints)

Todo endpoint declarado pelo Babble em `src/api/`, gerados a partir de
`api.core.registry`. Não edite o bloco: mude a declaração e execute
`python3 src/test-api-registry.py --write-table`; o teste `api registry` falha
enquanto o bloco estiver desatualizado. Mastodon é a versão que a documentação
nomeia para o endpoint (em branco quando não nomeia nenhuma).

A tabela abaixo é gerada a partir do código e não deve ser editada à mão; ela
é regenerada com `python3 src/test-api-registry.py --write-table`.

<!-- api-table:start -->
| Method | Path | Scope | Mastodon | Deprecated | Docs |
|---|---|---|---|---|---|
| GET | `/accounts` | none | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#index) |
| GET | `/accounts/%s` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#get) |
| POST | `/accounts/%s/block` | `write:blocks` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#block) |
| GET | `/accounts/%s/collections` | `read:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#get_collections) |
| POST | `/accounts/%s/endorse` | `write:accounts` | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#endorse) |
| GET | `/accounts/%s/endorsements` | none | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#endorsements) |
| GET | `/accounts/%s/featured_tags` | none | 3.3.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#featured_tags) |
| POST | `/accounts/%s/follow` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#follow) |
| GET | `/accounts/%s/followers` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#followers) |
| GET | `/accounts/%s/following` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#following) |
| GET | `/accounts/%s/in_collections` | `read:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#in_collections) |
| GET | `/accounts/%s/lists` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#lists) |
| POST | `/accounts/%s/mute` | `write:mutes` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#mute) |
| POST | `/accounts/%s/note` | `write:accounts` | 3.2.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#note) |
| POST | `/accounts/%s/pin` | `write:accounts` |  | endorse | [docs](https://docs.joinmastodon.org/methods/accounts/#pin) |
| POST | `/accounts/%s/remove_from_followers` | `write:follows` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#remove_from_followers) |
| GET | `/accounts/%s/statuses` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#statuses) |
| POST | `/accounts/%s/unblock` | `write:blocks` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unblock) |
| POST | `/accounts/%s/unendorse` | `write:accounts` | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unendorse) |
| POST | `/accounts/%s/unfollow` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unfollow) |
| POST | `/accounts/%s/unmute` | `write:mutes` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unmute) |
| POST | `/accounts/%s/unpin` | `write:accounts` |  | unendorse | [docs](https://docs.joinmastodon.org/methods/accounts/#unpin) |
| GET | `/accounts/familiar_followers` | `read:follows` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#familiar_followers) |
| GET | `/accounts/lookup` | `read:accounts` | 3.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#lookup) |
| GET | `/accounts/relationships` | `read:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#relationships) |
| GET | `/accounts/search` | `read:accounts` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#search) |
| PATCH | `/accounts/update_credentials` | `write:accounts` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#update_credentials) |
| GET | `/accounts/verify_credentials` | `read:accounts` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#verify_credentials) |
| GET | `/admin/accounts` | `admin:read:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#v1) |
| POST | `/admin/accounts/%s/action` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#action) |
| POST | `/admin/accounts/%s/approve` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#approve) |
| POST | `/admin/accounts/%s/enable` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#enable) |
| POST | `/admin/accounts/%s/reject` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#reject) |
| POST | `/admin/accounts/%s/unsensitive` | `admin:write:accounts` | 3.3.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#unsensitive) |
| POST | `/admin/accounts/%s/unsilence` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#unsilence) |
| POST | `/admin/accounts/%s/unsuspend` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#unsuspend) |
| POST | `/admin/dimensions` | `admin:read` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/dimensions/#get) |
| GET | `/admin/domain_allows` | `admin:read:domain_allows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_allows/#get) |
| POST | `/admin/domain_allows` | `admin:write:domain_allows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_allows/#create) |
| DELETE | `/admin/domain_allows/%s` | `admin:write:domain_allows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_allows/#delete) |
| GET | `/admin/domain_blocks` | `admin:read:domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_blocks/#get) |
| POST | `/admin/domain_blocks` | `admin:write:domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_blocks/#create) |
| DELETE | `/admin/domain_blocks/%s` | `admin:write:domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_blocks/#delete) |
| GET | `/admin/email_domain_blocks` | `admin:read:email_domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/email_domain_blocks/#get) |
| POST | `/admin/email_domain_blocks` | `admin:write:email_domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/email_domain_blocks/#create) |
| DELETE | `/admin/email_domain_blocks/%s` | `admin:write:email_domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/email_domain_blocks/#delete) |
| GET | `/admin/ip_blocks` | `admin:read:ip_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/ip_blocks/#get) |
| POST | `/admin/ip_blocks` | `admin:write:ip_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/ip_blocks/#create) |
| DELETE | `/admin/ip_blocks/%s` | `admin:write:ip_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/ip_blocks/#delete) |
| POST | `/admin/measures` | `admin:read` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/measures/#get) |
| GET | `/admin/reports` | `admin:read:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#get) |
| PUT | `/admin/reports/%s` | `admin:write:reports` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#update) |
| POST | `/admin/reports/%s/assign_to_self` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#assign_to_self) |
| POST | `/admin/reports/%s/reopen` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#reopen) |
| POST | `/admin/reports/%s/resolve` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#resolve) |
| POST | `/admin/reports/%s/unassign` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#unassign) |
| GET | `/announcements` | none | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#get) |
| POST | `/announcements/%s/dismiss` | `write:accounts` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#dismiss) |
| DELETE | `/announcements/%s/reactions/%s` | `write:favourites` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#delete-reactions) |
| PUT | `/announcements/%s/reactions/%s` | `write:favourites` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#put-reactions) |
| GET | `/annual_reports` | `read:accounts` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#get) |
| GET | `/annual_reports/%d` | `read:accounts` | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#get-one) |
| POST | `/annual_reports/%d/generate` | `write:accounts` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#generate) |
| GET | `/annual_reports/%d/state` | `read:accounts` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#state) |
| GET | `/api/v2/admin/accounts` | `admin:read:accounts` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#v2) |
| GET | `/api/v2/filters` | `read:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#get) |
| POST | `/api/v2/filters` | `write:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#create) |
| DELETE | `/api/v2/filters/%s` | `write:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#delete) |
| PUT | `/api/v2/filters/%s` | `write:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#update) |
| GET | `/api/v2/instance` | none | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/instance/#v2) |
| POST | `/api/v2/media` | `write:media` | 3.1.3 |  | [docs](https://docs.joinmastodon.org/methods/media/#v2) |
| GET | `/api/v2/notifications/policy` | `read:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#get-policy) |
| PATCH | `/api/v2/notifications/policy` | `write:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#update-the-filtering-policy) |
| GET | `/api/v2/search` | `read:search` | 2.4.1 |  | [docs](https://docs.joinmastodon.org/methods/search/#v2) |
| GET | `/api/v2/suggestions` | `read` | 3.4.0 |  | [docs](https://docs.joinmastodon.org/methods/suggestions/#v2) |
| GET | `/blocks` | `read:blocks` |  |  | [docs](https://docs.joinmastodon.org/methods/blocks/#get) |
| GET | `/bookmarks` | `read:bookmarks` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/bookmarks/#get) |
| POST | `/collections` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#create) |
| DELETE | `/collections/%s` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#delete_collection) |
| GET | `/collections/%s` | `read:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#get_collection) |
| PATCH | `/collections/%s` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#update_collection) |
| POST | `/collections/%s/items` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#add_account) |
| DELETE | `/collections/%s/items/%s` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#remove_account) |
| POST | `/collections/%s/items/%s/revoke` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#revoke_item) |
| GET | `/conversations` | `read:statuses` | 2.6.0 |  | [docs](https://docs.joinmastodon.org/methods/conversations/#get) |
| DELETE | `/conversations/%s` | `write:conversations` | 2.6.0 |  | [docs](https://docs.joinmastodon.org/methods/conversations/#delete) |
| POST | `/conversations/%s/read` | `write:conversations` | 2.6.0 |  | [docs](https://docs.joinmastodon.org/methods/conversations/#read) |
| GET | `/custom_emojis` | none | 2.0.0 |  | [docs](https://docs.joinmastodon.org/methods/custom_emojis/#get) |
| DELETE | `/domain_blocks` | `write:blocks` | 1.4.0 |  | [docs](https://docs.joinmastodon.org/methods/domain_blocks/#unblock) |
| GET | `/domain_blocks` | `read:blocks` | 1.4.0 |  | [docs](https://docs.joinmastodon.org/methods/domain_blocks/#get) |
| POST | `/domain_blocks` | `write:blocks` | 1.4.0 |  | [docs](https://docs.joinmastodon.org/methods/domain_blocks/#block) |
| GET | `/favourites` | `read:favourites` | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/favourites/#get) |
| GET | `/featured_tags` | `read:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#get) |
| POST | `/featured_tags` | `write:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#feature) |
| DELETE | `/featured_tags/%s` | `write:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#unfeature) |
| GET | `/featured_tags/suggestions` | `read:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#suggestions) |
| GET | `/follow_requests` | `read:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/follow_requests/#get) |
| POST | `/follow_requests/%s/authorize` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/follow_requests/#accept) |
| POST | `/follow_requests/%s/reject` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/follow_requests/#reject) |
| GET | `/followed_tags` | `read:follows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/followed_tags/#get) |
| GET | `/lists` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#get) |
| POST | `/lists` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#create) |
| DELETE | `/lists/%s` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#delete) |
| PUT | `/lists/%s` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#update) |
| DELETE | `/lists/%s/accounts` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#accounts-remove) |
| GET | `/lists/%s/accounts` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#accounts) |
| POST | `/lists/%s/accounts` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#accounts-add) |
| GET | `/markers` | `read:statuses` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/markers/#get) |
| POST | `/markers` | `write:statuses` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/markers/#create) |
| GET | `/media/%s` | `write:media` | 3.1.3 |  | [docs](https://docs.joinmastodon.org/methods/media/#get) |
| GET | `/mutes` | `read:mutes` |  |  | [docs](https://docs.joinmastodon.org/methods/mutes/#get) |
| GET | `/notifications` | `read:notifications` | 1.0.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#get) |
| POST | `/notifications/%s/dismiss` | `write:notifications` | 1.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#dismiss) |
| POST | `/notifications/clear` | `write:notifications` | 1.0.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#clear) |
| GET | `/notifications/requests` | `read:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#get-requests) |
| POST | `/notifications/requests/%s/accept` | `write:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#accept-request) |
| POST | `/notifications/requests/%s/dismiss` | `write:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#dismiss-request) |
| GET | `/notifications/unread_count` | `read:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#unread-count) |
| GET | `/polls/%s` | none | 2.8.0 |  | [docs](https://docs.joinmastodon.org/methods/polls/#get) |
| POST | `/polls/%s/votes` | `write:statuses` | 2.8.0 |  | [docs](https://docs.joinmastodon.org/methods/polls/#vote) |
| PATCH | `/profile` | `write:accounts` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/profile/#update) |
| POST | `/reports` | `write:reports` | 1.1.0 |  | [docs](https://docs.joinmastodon.org/methods/reports/#post) |
| GET | `/scheduled_statuses` | `read:statuses` | 2.7.0 |  | [docs](https://docs.joinmastodon.org/methods/scheduled_statuses/#get) |
| DELETE | `/scheduled_statuses/%s` | `write:statuses` | 2.7.0 |  | [docs](https://docs.joinmastodon.org/methods/scheduled_statuses/#cancel) |
| PUT | `/scheduled_statuses/%s` | `write:statuses` | 2.7.0 |  | [docs](https://docs.joinmastodon.org/methods/scheduled_statuses/#update) |
| POST | `/statuses` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#create) |
| DELETE | `/statuses/%s` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#delete) |
| GET | `/statuses/%s` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#get) |
| PUT | `/statuses/%s` | `write:statuses` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#edit) |
| POST | `/statuses/%s/bookmark` | `write:bookmarks` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#bookmark) |
| GET | `/statuses/%s/context` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#context) |
| POST | `/statuses/%s/favourite` | `write:favourites` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#favourite) |
| GET | `/statuses/%s/favourited_by` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#favourited_by) |
| GET | `/statuses/%s/history` | none | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#history) |
| PUT | `/statuses/%s/interaction_policy` | `write:statuses` | 4.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#update_interaction_policy) |
| POST | `/statuses/%s/mute` | `write:mutes` | 1.4.2 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#mute) |
| POST | `/statuses/%s/pin` | `write:accounts` | 1.6.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#pin) |
| GET | `/statuses/%s/quotes` | `read:statuses` | 4.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#quotes) |
| POST | `/statuses/%s/quotes/%s/revoke` | `write:statuses` | 4.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#revoke_quote) |
| POST | `/statuses/%s/reblog` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#boost) |
| GET | `/statuses/%s/reblogged_by` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#reblogged_by) |
| GET | `/statuses/%s/source` | `read:statuses` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#source) |
| POST | `/statuses/%s/translate` | `read:statuses` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#translate) |
| POST | `/statuses/%s/unbookmark` | `write:bookmarks` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unbookmark) |
| POST | `/statuses/%s/unfavourite` | `write:favourites` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unfavourite) |
| POST | `/statuses/%s/unmute` | `write:mutes` | 1.4.2 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unmute) |
| POST | `/statuses/%s/unpin` | `write:accounts` | 1.6.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unpin) |
| POST | `/statuses/%s/unreblog` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unreblog) |
| DELETE | `/suggestions/%s` | `read` | 2.4.3 |  | [docs](https://docs.joinmastodon.org/methods/suggestions/#remove) |
| GET | `/tags/%s` | none | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/tags/#get) |
| POST | `/tags/%s/follow` | `write:follows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/tags/#follow) |
| POST | `/tags/%s/unfollow` | `write:follows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/tags/#unfollow) |
| GET | `/timelines/home` | `read:statuses` | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#home) |
| GET | `/timelines/link` | none | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#link) |
| GET | `/timelines/list/%s` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#list) |
| GET | `/timelines/public` | none | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#public) |
| GET | `/timelines/tag/%s` | none | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#tag) |
| GET | `/trends/links` | none | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/trends/#links) |
| GET | `/trends/statuses` | none | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/trends/#statuses) |
| GET | `/trends/tags` | none | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/trends/#tags) |
<!-- api-table:end -->

## `configuration.timelines_access` (4.5+)

Três configurações, cada uma com lados `local`/`remote`. Só `live_feeds`
cobre as duas timelines da barra lateral que o Babble esconde; as duas
configurações de *tópico* não **condicionam** nada no Babble, por decisão
de projeto (veja
abaixo). Elas leem as mesmas duas configurações de administração
(`*_topic_feed_access`), então `hashtag_feeds` e `trending_link_feeds` sempre
carregam valores idênticos.

| Configuração | Efeito no servidor sobre uma conta conectada |
|---|---|
| `live_feeds` (`local`/`remote`) | Um lado definido como `disabled` fecha esse lado: `GET /timelines/public` volta vazio (ou fica a cargo do outro lado), a menos que o papel tenha "View live and topic feeds" (`0x100000`). |
| `hashtag_feeds` (`remote`; `local` é apenas `public`/`authenticated`) | Quando `remote` está `disabled`, o `TagFeed` **volta a ser apenas local** — `GET /timelines/tag/:hashtag` responde com publicações locais, nunca com um erro. |
| `trending_link_feeds` (`remote`; `local` do mesmo modo) | A mesma queda para apenas local no `LinkFeed` (`GET /timelines/link`). **Não** afeta `GET /trends/links`. |

Por isso, `streaming.public_feed_access` devolve apenas `local`/`federated`
(os dois feeds que a barra lateral pode esconder). As configurações de tópico
são informativas para uma futura matriz de capacidades — veja a Fase 5 de
`Docs/ROADMAP.MD` — e não devem ser usadas para esconder páginas de hashtag
ou de tendências, porque a conta ainda pode ler o conteúdo local delas.

## Coleções (4.6+, API v10)

A superfície completa de métodos está implementada em `fedicollections.py`:
CRUD, itens de associação, consentimento e o limite de criação. Os pontos de
integração do guia do cliente:

- **Consentimento** — `Account#feature_approval.current_user`; somente
  `automatic`/`manual` podem ser adicionados (`can_invite`),
  `denied`/`unknown`/`missing` são recusados.
- **Estados de membro** — `pending`/`accepted` são membros;
  `rejected`/`revoked` ficam ocultos (`_is_member`).
- **Compartilhamento** — o `Status#tagged_collections` de uma publicação é
  renderizado como um `CollectionCard` abaixo do corpo
  (`timeline._show_collections`), e uma URL `/collections/:id` é resolvida por
  `/api/v2/search?resolve=true` e abre a `CollectionPage` dentro do aplicativo
  (`pages.fedi_link_kind`, `pages.open_collection`, `pages.resolve_uri`).
- **Denúncia** — `POST /reports` carrega `collection_ids[]`
  (`report.build_report_payload`), oferecida pela ação "Denúncia" em uma
  Coleção (`fedicollections.CollectionActions.report`).
- **Identificador da conta** — `Account#invalid_handle` (4.7.0) mostra um ícone
  de aviso nas linhas de conta e um selo "Identificador não verificado" no
  perfil.

## Deliberadamente não descontinuadas

- **Envio de mídia** usa `POST /api/v2/media` (a v1 foi descontinuada em
  3.1.3). Imagens pequenas são processadas de forma síncrona (HTTP 200, `url`
  presente); as grandes voltam com 202 e `url: null`, e o compositor consulta
  `GET /api/v1/media/:id` até ela aparecer. Escopo OAuth `write:media`.
- **Filtros** usam a API v2 (resultados `filtered` do lado do servidor), nunca
  a correspondência da v1 do lado do cliente.
- **Escopos OAuth** são `read write` + `admin:read admin:write`, apenas para a
  autorização de moderação. O escopo `follow` está descontinuado desde o
  Mastodon 3.5; `read`/`write` concedem todos os escopos granulares de que o
  Babble precisa, coleções incluídas.

## Sugestões de hashtags no compositor

O compositor completa a `#hashtag` que está sendo digitada a partir de quatro
requisições. Nenhuma delas é condicionada por `api_versions`: um servidor que
não tem uma delas responde com um erro, que é tratado como lista vazia, e as
outras continuam funcionando.

| Requisição | Usada para | Se estiver ausente |
|---|---|---|
| `GET /api/v2/search?type=hashtags&q=…&limit=10&resolve=false` | Sugestões para o que foi digitado. `exclude_unreviewed` não é enviado: ele deixaria apenas o nome exato em um servidor cujo administrador não revisou nenhuma tag. | Sem sugestões do servidor; as tags da própria conta abaixo continuam aparecendo. Silencioso, sem aviso. |
| `GET /api/v1/featured_tags/suggestions` (3.0) | Tags que a conta usou recentemente (`Usada recentemente`) | Essa fonte fica vazia. |
| `GET /api/v1/featured_tags` | Tags no perfil (`No seu perfil`) | Essa fonte fica vazia. |
| `GET /api/v1/followed_tags?limit=200` (4.0) | Tags seguidas (`Seguida`); apenas uma página | Essa fonte fica vazia. |

As três listas são buscadas juntas na primeira vez que um `#` é digitado para
uma conta (o conjunto pertence ao cliente da conta), mantidas por dez minutos
(um minuto, se as três falharem), e descartadas quando uma tag é seguida,
deixada de seguir, destacada ou removida das destacadas. As tags de uma
publicação recém-enviada entram na hora. Uma consulta sem nenhuma letra
(`#2024`) não é enviada ao servidor.

## Tipos de notificação

O Babble renderiza os tipos documentados, incluindo as adições da 4.5
(`quote`, `quoted_update`) e as adições da 4.6 (`added_to_collection`,
`collection_update`, ambas v10), e ignora a API de notificações agrupadas do
servidor (uma escolha deliberada: o agrupamento é feito do lado do cliente).
Novos eventos de streaming tratados: `status.update`, `filters_changed` (2.4.3,
sem payload) e `notifications_merged` (4.3.0, payload ignorado). Ambos chegam
no stream `user`.

Dispensar uma notificação é `POST /api/v1/notifications/:id/dismiss` (Mastodon
1.3.0, escopo `write:notifications`). Não precisa de condição de capacidade: o
`POST /notifications/dismiss` mais antigo, com um parâmetro `id`, foi removido
em 3.0, e a documentação não lista nenhuma forma `DELETE`. Uma linha agrupada
manda uma requisição por notificação que ela contém.

## Paginação por um id próximo do que você tem

O Mastodon mantém um feed no Redis ordenado por um score em ponto flutuante, e
os ids passam de 2**53 (cerca de 9e15), então ao lado de um os vizinhos
distam 16. Pedir ``max_id`` um acima de um id, portanto, arredonda para aquele
id, e o limite exclusivo pula a publicação. Medido em um servidor ao vivo para a
posição de retomada: ``+1`` e ``+16`` a pularam, ``+32`` e mais a devolveram primeiro.
A posição de retomada soma 64 (``readingposition.anchor_params``). O mesmo
arredondamento pode, em princípio, afetar qualquer requisição que limited por
um id na borda de uma página, como ``min_id`` para publicações mais novas; não
foi visto perder uma publicação por causa disso.

## Vigilância de descontinuações

`mastodon.log_deprecation` registra qualquer cabeçalho `Deprecation` da
RFC 9745 (e um `Sunset` correspondente, quando presente) sob
`G_MESSAGES_DEBUG=Babble`, de modo que uma rota que o Mastodon aposenta aparece
em desenvolvimento, antes de quebrar em campo.
