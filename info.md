# PacketFence Project Information for Planner LLM

This file is an expanded planning context for the local PacketFence checkout at:

`packetfence/`

It is designed to be injected into a planner LLM that decomposes broad code-review,
code-search, debugging, or implementation requests into focused subqueries.

- Local `packetfence/README.md`, `Makefile`, `config.mk`, `go/go.mod`, package manifests, and directory inventory

Do not treat this file as a replacement for reading the relevant code. Treat it as a routing map:
it tells the planner where to look, what assumptions are unsafe, and what subqueries should be
spawned for each kind of change.

---

## 1. Product Summary

PacketFence is a Free and Open Source Network Access Control (NAC) system.

Core product capabilities visible from the repository and README:

- Captive portal for device registration and remediation.
- Centralized wired and wireless network access control.
- 802.1X support.
- Layer-2 isolation of problematic or unregistered devices.
- Integration with IDS systems, vulnerability scanners, authentication sources, firewalls, MDM/provisioning systems, and network devices.
- Large heterogeneous network support.

License:

- GNU GPL v2, see `packetfence/COPYING`.

Upstream identity:

- GitHub project: `inverse-inc/packetfence`.
- Main development branch referenced by README: `devel`.

---

## 2. Repository Scope and Shape

The local workspace contains a full PacketFence source tree under `packetfence/`.

The source tree is large and mixed-language. Observed file-type concentration:

- Perl modules: many `.pm` files; main backend is Perl.
- YAML/YML: tests, CI, scenarios, configuration data.
- JavaScript/Vue: admin UI and frontend code.
- Go: runtime services and helpers under `go/`.
- SQL: schema and database artifacts.
- Asciidoc/Markdown: product documentation.
- Shell/Perl scripts: packaging, upgrades, migration, operational scripts.
- FreeRADIUS/systemd/config templates: service runtime behavior.

High-level top-level directories:

- `addons/` - upgrade scripts, migration helpers, development helpers, integrations, import/export helpers.
- `bin/` - executables and helper scripts; includes Python NTLM auth API helpers and Perl/C utilities.
- `ci/` - CI, packer, installer, and test automation support.
- `conf/` - source configuration templates, defaults, systemd templates, locale files, generated config sources.
- `containers/` - container/runtime build artifacts and supporting scripts.
- `db/` - SQL schema, migrations, database artifacts.
- `debian/` - Debian packaging.
- `docs/` - Asciidoc product documentation and documentation build assets.
- `go/` - Go services, plugins, runtime helpers, API clients, config drivers.
- `html/` - web applications: admin UI, captive portal, common web assets.
- `lib/` - main Perl backend tree.
- `raddb/` - FreeRADIUS configuration.
- `rpm/` - RPM packaging specs.
- `sbin/` - service/runtime executables.
- `src/` - C and other source artifacts outside the main Perl tree.
- `t/` - Perl tests, Venom integration scenarios, mock services, frontend test support.
- `var/` - generated/runtime/project data.

Important root files:

- `README.md` - short product summary and contribution/support links.
- `Makefile` - documentation, development setup, certificates, generated files, service/config install targets, smoke test target.
- `config.mk` - install prefixes, source directory definitions, Go binary names, release extraction, package file lists.
- `NEWS.asciidoc`, `ChangeLog` - release history.
- `CONTRIBUTING.md` - contribution rules.
- `COPYING` - GPLv2 license.
- `.gitlab-ci.yml` - CI pipeline definitions.
- `patch.diff` - local diff artifact in the PacketFence tree; inspect when the task concerns current changes or review input.

---

## 3. Technology Stack

### Backend

Primary backend:

- Perl under `lib/pf/`.
- Mojolicious for Unified API controllers.
- HTML::FormHandler-based forms under `html/pfappserver/lib/pfappserver/Form/`.
- Custom ConfigStore abstraction over runtime configuration files.
- Custom DAL under `lib/pf/dal/`.
- FreeRADIUS integration under `raddb/` and `lib/pf/radius/`.
- System/service orchestration under `lib/pf/services/` and service files in `conf/systemd/` and root service artifacts.

### Go Runtime Components

Go module:

- `packetfence/go/go.mod`
- Module path: `github.com/inverse-inc/packetfence/go`
- Go version from `go.mod` and `config.mk`: `1.25.5`

Important Go areas:

- `go/cmd/` - Go command entry points.
- `go/dhcp/` - DHCP service logic.
- `go/db/`, `go/dal/` - database access helpers.
- `go/config/`, `go/pfconfigdriver/` - configuration/runtime config integration.
- `go/cron/` - cron-like runtime service behavior.
- `go/connector/` - remote connector support.
- `go/ntlm/` - NTLM-related helpers.
- `go/plugin/` - plugin support.
- `go/redisclient/`, `go/redis_cache/` - Redis clients/cache.
- `go/pfqueueclient/` - queue client support.
- `go/unifiedapiclient/` - API client support.

Go binaries listed in `config.mk`:

- `pfhttpd`
- `pfqueue-go`
- `pfdhcp`
- `pfdns`
- `pfstats`
- `pfdetect`
- `galera-autofix`
- `pfacct`
- `pfcron`
- `mysql-probe`
- `pfconnector`
- `sdnotify-proxy`
- `pfudpproxy`

Go command helpers:

- `pfcrypt`
- `pfkafka`

Notable Go dependencies:

- `gin-gonic/gin`
- `gorilla/mux`, `gorilla/websocket`
- `go-sql-driver/mysql`, `gorm`
- `redis/go-redis`
- `segmentio/kafka-go`
- `prometheus/client_golang`
- `miekg/dns`
- Kubernetes client libraries
- Caddy/CoreDNS libraries
- Inverse-maintained network/RADIUS/DHCP packages

### Frontend

Admin UI:

- Path: `html/pfappserver/root/`
- Package name: `packetfence-admin`
- Version in package manifest: `15.1.0`
- Vue version: `2.6.14`
- Uses Vue Router 3, Vuex 3, Pinia 2, Bootstrap 4, BootstrapVue, Axios, Yup, Plotly, vue-i18n.
- Scripts: `serve`, `build`, `lint`, `build-debug`, `build-report`.

Captive portal/common web assets:

- `html/captive-portal/`
- `html/common/`
- `html/common/package.json` package name: `packetfence-captive-portal`
- Uses Grunt, Sass, PostCSS, inuitcss, browser CSS tooling.

### Python

Python is present mostly for helper services/scripts, especially:

- `bin/pyntlm_auth/`
- `bin/impacket/`
- `containers/github-apps-token/requirements.txt`

Do not assume Python is the primary application stack.

### C / Native

Native artifacts include:

- `src/pfcmd.c`
- `src/ntlm_auth_wrap.c`
- `src/mariadb_udf/`

The root `Makefile` builds native helpers such as:

- `bin/pfcmd`
- `bin/ntlm_auth_wrapper`
- MariaDB UDF shared objects/tests

---

## 4. Main Backend Map

The main Perl backend is under:

- `lib/pf/`

Important subtrees:

- `lib/pf/UnifiedApi/` - main Mojolicious API layer.
- `lib/pf/ConfigStore/` - runtime configuration abstraction over INI/config files.
- `lib/pf/dal/` - database access layer.
- `lib/pf/services/` - service orchestration.
- `lib/pf/services/manager/` - service managers and lifecycle logic.
- `lib/pf/Switch/` - network switch/vendor/model integrations.
- `lib/pf/Authentication/` - authentication source and auth logic.
- `lib/pf/Connection/` - connection/profile logic.
- `lib/pf/radius/` - RADIUS helpers and integrations.
- `lib/pf/domain/` - AD/domain-related logic.
- `lib/pf/scan/` - vulnerability scanning integrations.
- `lib/pf/pfcron/` - scheduled task modules.
- `lib/pf/pfqueue/` - queue system.
- `lib/pf/web/` - web helpers and dispatch logic.
- `lib/pf/WebAPI/` - older/legacy web API components.
- `lib/pf/config/` - config builders/helpers.
- `lib/pf/util/` - shared utility modules.
- `lib/pf/constants/` - shared constants.
- `lib/pf/task/` - task modules.
- `lib/pf/provisioner/` - device provisioning integrations.
- `lib/pf/filter_engine/` - filter engines.
- `lib/pf/dhcp/` - DHCP-related Perl logic.
- `lib/pf/detect/` - detection logic.
- `lib/pf/Portal/` - portal-related backend logic.

Planner rule:

When a user asks about behavior, do not route only by filename. PacketFence behavior commonly crosses:

1. API controller
2. form/validation layer
3. ConfigStore or DAL layer
4. domain/helper module
5. service reload/cache/access side effect
6. frontend schema/form/view
7. upgrade/default config/template
8. integration tests or Venom scenario

---

## 5. Unified API

Path:

- `lib/pf/UnifiedApi/`

Observed structure:

- `Controller.pm` - base Mojolicious controller.
- `Controller/Crud.pm` - DAL-backed CRUD base.
- `Controller/Config.pm` - ConfigStore/form-backed config controller base.
- `Controller/RestRoute.pm` - REST route base.
- `Plugin/RestCrud.pm` - route/action registration plugin.
- `OpenAPI/Generator*.pm` - OpenAPI spec generation.
- `Search/Builder*.pm` - search/query building.
- `Controller/*` - concrete API controllers.

The local tree contains roughly 115 files under `lib/pf/UnifiedApi`.

Important controller families:

- `Controller/Config/*.pm` - config-backed controllers.
- `Controller/Fingerbank/*.pm` - Fingerbank resource controllers.
- `Controller/Users*.pm` - users/person/password/node relations.
- `Controller/Nodes.pm` - node lifecycle and node actions.
- `Controller/Services*.pm`, `Controller/SystemServices.pm` - service actions/status.
- `Controller/Authentication.pm` - auth-related API.
- `Controller/Reports.pm`, `Controller/DynamicReports.pm` - reporting APIs.
- `Controller/Pfqueue.pm`, `Controller/Queues.pm` - queue APIs.
- `Controller/*AuditLogs.pm`, `Controller/Ip4logs.pm`, `Controller/Ip6logs.pm`, `Controller/Locationlogs.pm` - log/history resources.

Critical behavior:

- Controllers are not thin wrappers.
- They may parse/validate input, normalize data, transform output, orchestrate bulk operations, perform domain logic directly, and trigger side effects.
- Business logic may live inside controllers.

Two main controller types:

1. Config-backed:
   - Usually extends `pf::UnifiedApi::Controller::Config`.
   - Has `config_store_class`.
   - Has `form_class`.
   - Uses ConfigStore and forms for validation, normalization, options, output cleanup.
   - Requires `commit()` after mutations.

2. DAL/domain-based:
   - Usually extends `pf::UnifiedApi::Controller::Crud` or `RestRoute`.
   - Has `dal`, `primary_key`, `url_param_name`, and optional search builder.
   - May directly call domain functions.
   - Often implements custom actions and bulk operations.
   - Must manage side effects explicitly.

Planner rule:

For any API-related task, first classify the controller as:

- Config-backed
- DAL-backed Crud
- Custom RestRoute/domain controller
- Fingerbank model-backed
- Service/action controller

Then route subqueries accordingly.

---

## 6. Config Layer

Paths:

- `lib/pf/ConfigStore/`
- `conf/`
- `var/`
- `html/pfappserver/lib/pfappserver/Form/`

Observed ConfigStore files include:

- `AdminRoles.pm`
- `BillingTiers.pm`
- `Cloud.pm`
- `Connector.pm`
- `Cron.pm`
- `DhcpFilters.pm`
- `DNS_Filters.pm`
- `Domain.pm`
- `EventLogger.pm`
- `FilterEngine.pm`
- `FingerbankSettings.pm`
- `Firewall_SSO.pm`
- `FloatingDevice.pm`
- `Interface.pm`
- `Kafka.pm`
- `L2Network.pm`
- `Mfa.pm`
- `Network.pm`
- `NetworkBehaviorPolicy.pm`
- `Pf.pm`
- `Pfdetect.pm`
- `PKI_Provider.pm`
- `PortalModule.pm`
- `Profile.pm`
- `Provisioning.pm`
- `RadiusFilters.pm`
- `Realm.pm`
- `Roles.pm`
- `RoutedNetwork.pm`
- `Scan.pm`
- `SecurityEvents.pm`
- `SelfService.pm`
- `Source.pm`
- `SSLCertificate.pm`
- `Switch.pm`
- `SwitchGroup.pm`
- `Syslog.pm`
- `TemplateSwitch.pm`
- `VlanFilters.pm`

Important facts:

- Config is a runtime system, not simple file IO.
- `conf/` contains source templates and default/static configuration.
- `var/` contains generated/runtime config.
- Controllers use forms plus ConfigStore for validation and normalization.
- `commit()` is required after mutations.
- `commit()` may trigger runtime side effects, cache updates, config rebuilds, service reload implications, or generated file changes.
- Inheritance/default/import behavior affects reads and writes.
- `skip_inheritance` changes read semantics.
- Deletion may be represented by `undef` or section removal depending on ConfigStore behavior.
- Output is frequently transformed through form processing.

Common transformation methods to inspect:

- `cleanup_item`
- `cleanup_items`
- `cleanupItemForGet`
- `cleanupItemForCreate`
- `cleanupItemForUpdate`
- `ensure_integer_fields`
- `_cleanup_placeholder`
- `form_process_parameters_for_cleanup`
- `form_process_parameters_for_validation`

Planner subqueries for config-backed changes:

- Identify `Controller::Config::*` class and its `config_store_class`, `form_class`, `primary_key`.
- Inspect matching `lib/pf/ConfigStore/*.pm`.
- Inspect matching `html/pfappserver/lib/pfappserver/Form/**/*.pm`.
- Inspect related frontend schema/view under `html/pfappserver/root/src/views/Configuration/**`.
- Inspect `conf/*.conf`, `conf/*.defaults`, `conf/*.example`, and any generated template.
- Inspect upgrade scripts if a persisted config shape changes.
- Check whether mutation path calls `commit()` exactly where required.
- Check whether `commit()` has subclass side effects.
- Check whether API output shape changed through cleanup/form processing.

---

## 7. Forms and Validation

Primary paths:

- `html/pfappserver/lib/pfappserver/Form/`
- `html/pfappserver/lib/pfappserver/Base/Form.pm`
- `html/pfappserver/lib/pfappserver/Base/Model/Config.pm`
- `html/pfappserver/root/src/views/**/schema.js`

Important facts:

- Forms are not only UI forms; they define backend validation, normalization, options, and sometimes API output shape.
- Config-backed Unified API controllers process forms for both validation and cleanup.
- A form field type change can change API response types.
- `ensure_integer_fields` exists because config values may be strings while form defaults may be integers.
- `render_list`, field definitions, dynamic option builders, and form roles may affect both UI and API.

Validation may live in:

- forms
- controller code
- domain functions
- parsers
- ConfigStore logic
- frontend schema validation

Planner rule:

Do not assume missing form validation is a bug until controller/domain/parser validation has been checked.

---

## 8. Database Layer

Path:

- `lib/pf/dal/`

Observed structure:

- Roughly 97 files.
- Entity modules and generated/underscore modules coexist.
- Examples: `node.pm`, `person.pm`, `security_event.pm`, `radius_audit_log.pm`, `ip4log.pm`, `ip6log.pm`, `locationlog.pm`, `pki_*`, `switch_observability*`.

Important facts:

- DAL is a thin abstraction over SQL.
- Controllers often use DAL directly.
- DAL operations may bypass domain validation and side effects.
- Generated underscore modules likely represent schema-generated base behavior; non-underscore modules may override or add behavior.

Planner subqueries for DAL changes:

- Inspect entity DAL module and matching underscore module.
- Inspect SQL schema in `db/`.
- Inspect API controller using this DAL.
- Inspect domain functions that normally wrap this entity.
- Inspect search builder if list/search behavior changes.
- Check whether write operations bypass required domain validation or side effects.
- Check bulk update/delete behavior for partial failure handling.

---

## 9. Service Management and Runtime Processes

Paths:

- `lib/pf/services/`
- `lib/pf/services/manager/`
- `conf/systemd/`
- root `packetfence*.service`, `*.init`, `*.logrotate`, `*.sudoers`
- `sbin/`
- `go/cmd/`

Important facts:

- PacketFence manages many cooperating services.
- There is systemd integration and service lifecycle orchestration.
- Some service managers are composite and delegate to submanagers.
- API endpoints can start/stop/restart services.
- Config changes may require service reload/restart or cache/config regeneration.

Review concerns:

- systemd unit name assumptions
- optional vs managed services
- start/stop/restart semantics
- async vs sync service actions
- composite/submanager consistency
- reload after config mutation
- error messages/status consistency
- service status mapping

Planner subqueries for service changes:

- Inspect relevant manager under `lib/pf/services/manager/`.
- Inspect `lib/pf/services/*.pm`.
- Inspect service controller under `lib/pf/UnifiedApi/Controller/*Services*.pm`.
- Inspect corresponding systemd template/service files.
- Inspect Go binary if the managed service is Go-based.
- Inspect tests/scenarios that start/stop/restart services.

---

## 10. Captive Portal

Paths:

- `html/captive-portal/`
- `lib/pf/Portal/`
- `html/common/`
- related profile/source/provisioning modules

Important facts:

- Captive portal is a separate stack from Unified API/admin UI.
- It is session/profile-driven.
- It has different controller style and request flow.
- Authentication, registration, remediation, provisioning, billing, and localization may cross several modules.
- Portal templates and profile templates can affect behavior.

Review concerns:

- session state transitions
- profile-dependent behavior
- auth source selection
- registration/unregistration side effects
- remediation and security event handling
- portal module ordering
- localization
- user-visible error flow
- interaction with connection profiles and sources

Planner subqueries for portal changes:

- Inspect portal templates/components in `html/captive-portal/`.
- Inspect `lib/pf/Portal/`.
- Inspect connection profile ConfigStore/form/frontend schema if profile behavior changes.
- Inspect authentication source modules under `lib/pf/Authentication/` and source forms/config.
- Inspect integration tests under `t/venom/scenarios/captive_portal` and `t/venom/test_suites/captive_portal`.

---

## 11. Admin UI

Paths:

- `html/pfappserver/root/src/`
- `html/pfappserver/root/src/views/Configuration/**`
- `html/pfappserver/lib/pfappserver/`

Important facts:

- Admin UI is Vue 2.
- Configuration views often have:
  - `_router.js`
  - `_api.js`
  - `_store.js`
  - `schema.js`
  - `_components/TheView.js`
  - `_components/TheForm.vue`
  - `_composables/useResource.js` or `useCollection.js`
- Backend form changes can affect frontend schemas and UI expectations.
- Frontend schema changes can reveal expected API shape and validation.

Planner subqueries for admin UI changes:

- Inspect view directory under `html/pfappserver/root/src/views/...`.
- Inspect matching backend Unified API controller.
- Inspect matching backend form.
- Inspect matching ConfigStore/DAL layer.
- Inspect translation/localization keys if labels/messages changed.
- Check whether list/search/detail/bulk actions stay consistent with API response shape.

---

## 12. Network Device, RADIUS, VLAN, and Enforcement Logic

Paths:

- `lib/pf/Switch/`
- `lib/pf/radius/`
- `raddb/`
- `lib/pf/role/`, `lib/pf/roles/`
- `lib/pf/inline/`
- `lib/pf/vlan`-related utilities/modules where present
- `conf/`, especially network/switch/radius templates
- `t/venom/scenarios/*dot1x*`, `*mac_auth*`, `*inline*`

Important facts:

- Network access control decisions can involve roles, switches, VLANs, RADIUS attributes, connection profiles, security events, and node state.
- Vendor switch integrations may override behavior deeply.
- FreeRADIUS config and Perl/Go runtime behavior must align.

Review concerns:

- RADIUS reply attributes
- VLAN/role assignment
- MAC authentication and 802.1X behavior
- dynamic VLAN behavior
- switch group/template inheritance
- vendor-specific switch behavior
- backward compatibility with existing configs
- service reload/restart after config changes

Planner subqueries:

- Inspect `lib/pf/Switch/<vendor or model>.pm` and base switch classes.
- Inspect `lib/pf/radius/`.
- Inspect `raddb/` templates/sites/modules.
- Inspect role/security event/connection profile code.
- Inspect Venom scenarios for dot1x/mac_auth/inline.

---

## 13. Nodes, Users, Roles, and Access Reevaluation

Important paths:

- `lib/pf/UnifiedApi/Controller/Nodes.pm`
- `lib/pf/UnifiedApi/Controller/Users.pm`
- `lib/pf/dal/node.pm`
- `lib/pf/dal/person.pm`
- `lib/pf/node*.pm` where present
- `lib/pf/person*.pm` where present
- `lib/pf/role/`, `lib/pf/roles/`
- `lib/pf/Connection/`
- `lib/pf/filter_engine/`

Important facts:

- Node and user changes can require access reevaluation, cache invalidation, role recalculation, firewall SSO updates, security event changes, or accounting/log updates.
- API cleanup may hide sensitive data, e.g. password fields may become flags such as `has_password`.
- Bulk operations may report per-item results.

Planner subqueries:

- Inspect API controller and DAL.
- Inspect domain functions for node/person mutation.
- Search for `reevaluate_access`, cache updates, security event updates, role recalculation.
- Inspect audit/log side effects.
- Inspect bulk response shape.

---

## 14. Fingerbank and Device Profiling

Paths:

- `lib/pf/UnifiedApi/Controller/Fingerbank*.pm`
- `go/fbcollectorclient/`
- Fingerbank-related config/form/frontend directories
- `t/venom/test_suites/device_profiling_virtualswitch`
- `t/venom/scenarios/fingerbank_invalid_db`

Important facts:

- Fingerbank helps identify devices from DHCP fingerprints, MAC vendors, user agents, combinations, and related metadata.
- Fingerbank controllers use Fingerbank models rather than only PacketFence DAL.
- Device profiling tests exist in Venom scenarios.

Planner subqueries:

- Inspect specific Fingerbank controller/model mapping.
- Inspect cleanup/output transformation in `Controller/Fingerbank.pm`.
- Inspect device profiling scenarios if behavior affects detection.

---

## 15. Queues, Cron, Events, and Async Work

Paths:

- `lib/pf/pfqueue/`
- `lib/pf/pfcron/`
- `go/cron/`
- `go/pfqueueclient/`
- `lib/pf/UnifiedApi/Controller/Pfqueue.pm`
- `lib/pf/UnifiedApi/Controller/Queues.pm`
- `lib/pf/task/`
- `lib/pf/ConfigStore/Cron.pm`
- `html/pfappserver/lib/pfappserver/Form/Config/Pfcron/`

Review concerns:

- task idempotence
- retry/partial failure
- queue status mapping
- async API response semantics
- side effects delayed through background jobs
- cron config validation and generated runtime config

Planner subqueries:

- Inspect task producer and consumer paths.
- Inspect queue client/service.
- Inspect config/form for scheduled tasks.
- Inspect API actions and response status semantics.

---

## 16. Upgrade and Migration Scripts

Path:

- `addons/upgrade/`

Important facts:

- Upgrade scripts often manipulate config directly.
- Idempotence is critical.
- Scripts must handle already-modified, partially migrated, or missing configs.
- Versioned upgrade names show historical migrations, e.g. `to-10.x-*`, `to-11.x-*`, `to-12.0-*`.

Review concerns:

- idempotence
- safe defaults
- preserving existing custom config
- compatibility with old and already-migrated config
- direct config manipulation correctness
- service reload/rebuild requirements

Planner subqueries:

- If config schema/defaults change, inspect whether an upgrade script is required.
- Inspect nearby upgrade scripts for established patterns.
- Inspect `addons/functions/configuration.functions` and related helper functions.
- Inspect docs/upgrade guide if user-facing behavior changes.

---

## 17. Tests and Verification

Paths:

- `t/`
- `t/*.t` - Perl tests/smoke tests.
- `t/venom/` - Venom integration scenarios and reusable test libraries.
- `t/venom/scenarios/` - scenario-level tests.
- `t/venom/test_suites/` - test suites by feature.
- `t/mock_servers/` - mock services.
- `t/html/pfappserver/` - frontend/admin UI test support.
- `go/**` - Go package tests may be colocated.

Root Makefile test target:

- `make test` runs `cd t && ./smoke.t`.

Useful Venom scenario areas:

- `captive_portal`
- `cluster`
- `dot1x_eap_peap`
- `dot1x_eap_tls`
- `dot1x_wired_computer_auth_virtualswitch`
- `mac_auth`
- `inline`
- `inline_l2_and_radius`
- `device_profiling_virtualswitch`
- `configurator`
- `backup_db_and_restore`
- `cli-login-radius`
- `external_integrations`

Planner test routing:

- API/controller change: search for Perl `.t` plus Venom reusable API steps under `t/venom/lib/pf_api_*`.
- Config/admin UI change: inspect corresponding `html/pfappserver` frontend tests if present, plus Venom config scenarios.
- RADIUS/network enforcement change: inspect dot1x/mac_auth/inline Venom scenarios.
- Captive portal change: inspect captive portal Venom scenario and test suite.
- Go service change: inspect Go package tests and service-level Venom scenarios.
- Upgrade script change: inspect/add idempotence-oriented script tests if local patterns exist.

---

## 18. Build, Packaging, and Generated Artifacts

Root `Makefile` responsibilities include:

- Documentation generation: PDF/HTML from `docs/PacketFence_*.asciidoc`.
- CSS minification for docs.
- Config initialization from `*.example` files.
- Local secrets/certificates generation.
- Native helper compilation.
- FreeRADIUS cert/site setup.
- Translation compilation.
- MySQL schema symlink setup.
- Permission fixing through `pfcmd`.
- Development setup target `devel`.
- Smoke test target `test`.
- HTML/package install targets.

`config.mk` defines:

- Install prefix defaults: `/usr/local/pf`, `/usr/local/pfconnector-remote`, `/usr/local/ntlm-auth-api`.
- Source directory variables.
- Package file include/exclude lists.
- Go binary list and Go version.
- Release version extraction from `conf/pf-release`.

Packaging paths:

- `debian/`
- `rpm/`
- root service/init/logrotate/sudoers artifacts

Planner rule:

If a change affects installed paths, package content, service files, generated configs, or runtime binaries,
include packaging/build subqueries.

---

## 19. API Output and Contract Transformation

Returned JSON often does not match storage fields one-to-one.

Output can be changed by:

- controller cleanup methods
- form processing
- default/inheritance resolution
- field type coercion
- hidden/sensitive field handling
- computed flags such as non-deletable/default/status indicators
- DAL/domain formatting
- OpenAPI generator metadata

High-risk methods:

- `cleanup_item`
- `cleanup_items`
- `cleanupItemForGet`
- `cleanupItemForCreate`
- `cleanupItemForUpdate`
- `ensure_integer_fields`
- `item_from_store`
- `format_form_errors`
- `options_from_form`
- `additional_create_out`
- `update_response`
- search builder formatting methods

Planner rule:

For any API response or frontend schema task, route one subquery to inspect API cleanup/output shape and another to inspect the consuming frontend/schema/test expectations.

---

## 20. Bulk Operation Semantics

Bulk endpoints often:

- return HTTP 200 even when some items fail
- return per-item results under `items`
- use mixed status formats, including ints or strings depending on existing endpoint conventions
- include per-item `message`, `warnings`, or metadata
- apply side effects per item rather than all-or-nothing

Do not assume:

- transaction-like behavior
- all-or-nothing success
- uniform status format across endpoints
- one global error response for partial failure

Planner subqueries for bulk changes:

- Inspect base bulk implementation in controller superclass.
- Inspect existing endpoint-specific bulk callbacks.
- Inspect response shape in nearby endpoints.
- Inspect tests or frontend bulk consumers.
- Inspect side effects per item.

---

## 21. Mutation Side Effects

After create/update/delete, check for required side effects.

Common side effects:

- `commit()` for ConfigStore changes.
- cache/lookup invalidation.
- generated config rebuild.
- service reload/restart.
- access reevaluation.
- node/user/role recalculation.
- firewall SSO update.
- security event reevaluation.
- related entity update.
- queue task creation.
- audit/log record creation.

Names and concepts worth searching:

- `commit`
- `reevaluate_access`
- `cache`
- `lookup`
- `reload`
- `restart`
- `pfqueue`
- `security_event`
- `role`
- `firewall`
- `sso`
- `radius`
- `node`
- `person`

Planner rule:

If a mutation writes data but does not show side effects, route a subquery specifically asking:
"What side effects do existing equivalent mutations trigger?"

---

## 22. Path-Specific Review Heuristics

### `lib/pf/UnifiedApi/`

Check:

- controller type
- base class behavior
- route/action registration
- validation path
- output cleanup
- bulk response consistency
- OpenAPI generator impact
- side effects after mutation
- frontend consumers

### `lib/pf/ConfigStore/`

Check:

- inheritance/default behavior
- import/export behavior
- deletion via `undef` or section removal
- commit side effects
- generated config interactions
- read/write semantic differences with `skip_inheritance`

### `lib/pf/dal/`

Check:

- schema alignment
- generated vs override module behavior
- domain validation bypass
- unsafe bulk update/delete
- missing side effects
- search/list assumptions

### `lib/pf/services/manager/`

Check:

- systemd assumptions
- start/stop/restart logic
- optional vs required services
- composite/submanager consistency
- async service action behavior
- status mapping

### `html/pfappserver/`

Check:

- form validation and normalization
- field types and defaults
- frontend schema/API alignment
- UI labels/translations
- downstream API output changes

### `html/captive-portal/`

Check:

- session flow
- profile-dependent behavior
- registration/auth side effects
- error and remediation flow
- localization/templates

### `addons/upgrade/`

Check:

- idempotence
- compatibility with already-modified configs
- direct config write correctness
- version ordering
- old config shape handling

### `conf/`

Check:

- runtime config impact
- template/default interactions
- generated files
- service reload requirements
- packaging/install inclusion

### `raddb/`

Check:

- FreeRADIUS module/site compatibility
- RADIUS attributes
- auth/accounting flow
- service reload requirements
- dot1x/mac_auth tests

### `go/`

Check:

- command entry point
- package ownership
- config driver/API client interactions
- service lifecycle integration
- concurrency/error/retry behavior
- Go tests and Venom integration scenarios

### `db/`

Check:

- schema compatibility
- migration needs
- DAL generation/underscore modules
- upgrade scripts
- default data

---

## 23. Planner Decomposition Strategy

Given a user request or diff, the planner should:

1. Identify touched paths.
2. Map each path to a subsystem using this file.
3. Classify the behavioral surface:
   - API contract
   - config persistence
   - database persistence
   - frontend/admin UI
   - captive portal
   - service lifecycle
   - network/RADIUS enforcement
   - upgrade/migration
   - tests/CI/build/package
4. Spawn subqueries that follow cross-layer ownership rather than file-only ownership.
5. Include one side-effect query for every mutation-related change.
6. Include one test-impact query for user-facing, API-facing, or runtime behavior changes.
7. Include one backward-compatibility query for config, DB, upgrade, RADIUS, or API contract changes.

Recommended subquery template:

```json
{
  "original_question": "<the user's request verbatim>",
  "subqueries": [
    {
      "id": "short_snake_case_id",
      "vector_query": "Natural-language semantic query for embedding/vector search.",
      "bm25_query": "Compact exact-token query for BM25: symbols filenames classes methods config keys.",
      "extensions": [".pm", ".js"],
      "keywords": ["exact_symbol", "config_key", "method_name"],
      "path_hints": ["lib/pf/UnifiedApi/Controller/Config/"],
      "top_k": 15
    }
  ],
  "answer_focus": [
    "Explain the relevant call path before conclusions.",
    "Pay attention to mutation side effects, validation, output contracts, and tests.",
    "Call out missing evidence when retrieved sources do not cover a required layer."
  ],
  "final_top_k": 10
}
```

Subquery rules:

- Use `vector_query` for meaning, behavior, and relationships.
- Use `bm25_query` for exact lexical matches such as class names, method names, config keys, filenames, and PacketFence-specific terms.
- Use `extensions` to bias retrieval toward likely languages/file types, not as the only signal.
- Use `keywords` for exact terms that should boost ranking when they appear in chunk metadata or paths.
- Use `path_hints` as repository-relative prefixes suitable for grep/file narrowing.
- Default each subquery `top_k` to `15`.
- The final merged retrieval should keep `10` chunks by default.
- Use `answer_focus` to tell the final-answer LLM what to emphasize after retrieval:
  call paths, contracts, side effects, tests, risk areas, and uncertainty boundaries.

---

## 24. Common Subquery Recipes

### API Config Endpoint Change

Ask subagents to inspect:

- `lib/pf/UnifiedApi/Controller/Config*.pm`
- concrete `Controller/Config/<Feature>.pm`
- `lib/pf/ConfigStore/<Feature>.pm`
- `html/pfappserver/lib/pfappserver/Form/Config/<Feature>.pm`
- `html/pfappserver/root/src/views/Configuration/<feature>/`
- relevant `conf/` defaults/templates
- `addons/upgrade/` if stored shape changes
- tests under `t/` and `t/venom/lib/pf_api_*`

Key questions:

- Is `commit()` called?
- Does cleanup change API contract?
- Do form field types/defaults match frontend schema?
- Are side effects and reloads preserved?
- Are inheritance/default semantics preserved?

### DAL Entity Change

Ask subagents to inspect:

- `lib/pf/dal/<entity>.pm`
- `lib/pf/dal/_<entity>.pm`
- `db/` schema
- controller using the DAL
- domain wrappers around the entity
- search builder and list endpoint
- tests for entity CRUD/bulk behavior

Key questions:

- Does this bypass validation?
- Are writes consistent with domain APIs?
- Are bulk semantics safe?
- Is schema compatible?

### Service Manager Change

Ask subagents to inspect:

- manager module
- service registry/config
- systemd files
- Unified API service controller
- Go/Perl executable entry point
- integration tests that start/stop/restart service

Key questions:

- Is status mapping correct?
- Is async behavior handled?
- Are optional services treated correctly?
- Are errors propagated consistently?

### Admin UI Configuration Change

Ask subagents to inspect:

- Vue view directory
- frontend schema/store/api module
- backend Unified API controller
- backend form
- ConfigStore
- translations
- tests

Key questions:

- Does UI send fields accepted by backend?
- Does UI expect fields returned by cleanup/form processing?
- Are defaults/options loaded from backend form endpoints?

### Captive Portal Change

Ask subagents to inspect:

- portal route/template/controller flow
- profile/source/provisioning ConfigStore and forms
- session handling
- node/user/security event side effects
- captive portal Venom tests

Key questions:

- Does registration/auth flow still transition state correctly?
- Does profile-dependent behavior still match config?
- Are user-facing errors/localization handled?

### RADIUS / Switch / Enforcement Change

Ask subagents to inspect:

- `lib/pf/radius/`
- `raddb/`
- `lib/pf/Switch/`
- roles/security events/connection profiles
- network config defaults
- Venom dot1x/mac_auth/inline tests

Key questions:

- Are RADIUS reply attributes correct?
- Are VLAN/role decisions preserved?
- Do vendor-specific overrides still work?
- Are service reloads required?

### Upgrade Script Change

Ask subagents to inspect:

- target upgrade script
- nearby upgrade scripts
- affected ConfigStore/forms/defaults
- old and new config shapes
- idempotence patterns

Key questions:

- Can the script run twice?
- Does it preserve custom config?
- Does it handle missing/partial/old values?
- Is the version/order correct?

---

## 25. High-Risk Assumptions to Avoid

Do not assume:

- Controllers are thin wrappers.
- Business logic always lives outside controllers.
- ConfigStore is simple file IO.
- `conf/` is the runtime source of truth.
- `var/` is irrelevant.
- DAL writes trigger domain validation.
- Missing form validation is automatically a bug.
- API response fields equal storage fields.
- Bulk endpoints are all-or-nothing.
- HTTP 200 means every item succeeded.
- `commit()` is optional after ConfigStore mutations.
- UI schema and backend form can be changed independently.
- RADIUS/config changes are safe without service reload implications.
- Upgrade scripts only run on pristine configs.
- Generated underscore DAL modules are the only implementation to inspect.

---

## 26. Search Hints

Use `rg` first.

Useful searches:

```bash
rg -n "cleanup_item|cleanupItemForGet|cleanupItemForCreate|cleanupItemForUpdate|ensure_integer_fields" lib/pf/UnifiedApi
rg -n "commit\\(" lib/pf/UnifiedApi lib/pf/ConfigStore html/pfappserver/lib
rg -n "reevaluate_access|security_event|pfqueue|cache|reload|restart" lib/pf html/pfappserver/lib go
rg -n "has 'config_store_class'|has 'form_class'|has dal|primary_key|url_param_name" lib/pf/UnifiedApi/Controller
rg -n "has_field|render_list|validate_|options_" html/pfappserver/lib/pfappserver/Form
rg -n "<api_or_field_name>" html/pfappserver/root/src/views lib/pf html/pfappserver/lib conf db t
rg -n "<service_name>" lib/pf/services conf/systemd go sbin bin t
rg -n "<radius_or_vlan_term>" lib/pf/radius lib/pf/Switch raddb conf t/venom
```

For file discovery:

```bash
rg --files packetfence
rg --files packetfence/lib/pf/UnifiedApi
rg --files packetfence/html/pfappserver/root/src/views
rg --files packetfence/t/venom
```

---

## 27. Expected Planner Output

When this file is used as prompt context, the planner should output subrequests that are:

- path-scoped
- subsystem-aware
- cross-layer where necessary
- explicit about risks
- explicit about side effects
- explicit about tests
- explicit about assumptions

Bad subrequest:

```text
Review the controller.
```

Good subrequest:

```text
Review the Config-backed Unified API change for network behavior policies.
Inspect the concrete controller, its ConfigStore, backend form, frontend schema/view,
and any defaults or upgrade scripts. Determine whether validation, cleanup output,
commit side effects, and API/frontend contract remain consistent. Return specific
files and tests to run or add.
```

---

## 28. Minimal Mental Model

PacketFence is a large NAC platform where many changes are cross-cutting:

- API controllers often own real business logic.
- ConfigStore plus forms define runtime config behavior and API shape.
- DAL is thin and can bypass validation.
- Mutation correctness depends on side effects, not only data writes.
- Admin UI schemas and backend forms are tightly coupled.
- Captive portal flow is separate from admin API flow.
- Network enforcement depends on RADIUS, roles, switches, node state, security events, and config reloads.
- Upgrade scripts must preserve existing deployments.

Planner priority:

For every change, identify the behavioral contract first, then ask targeted subqueries along the full call path.
