####################
# Makefile Options #
####################
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := all

########################
# Marketplace Settings #
########################
MARKETPLACE_NAME := cross-ts
OWNER_NAME := cross-ts

#########
# Tasks #
#########
PLUGIN_MANIFESTS := $(wildcard plugins/*/plugin.json)
MARKETPLACE_MANIFESTS := $(shell find . -type f -name marketplace.json)

.PHONY: all
all: $(MARKETPLACE_MANIFESTS)

.agents/plugins/marketplace.json: $(PLUGIN_MANIFESTS) $(wildcard plugins/*/.codex-plugin/plugin.json) Makefile
	@jq -s \
		--arg name "$(MARKETPLACE_NAME)" \
		'{
			name: $$name,
			plugins: unique_by(.name) | map({
				name: .name,
				source: {
					source: "local",
					path: "./plugins/" + .name
				},
				policy: {
					installation: "AVAILABLE",
					authentication: "ON_INSTALL"
				},
				category: "Productivity"
			})
		}' \
		$(filter-out Makefile,$^) </dev/null > "$@"

.claude-plugin/marketplace.json: $(PLUGIN_MANIFESTS) $(wildcard plugins/*/.claude-plugin/plugin.json) Makefile
	@jq -s \
		--arg name "$(MARKETPLACE_NAME)" \
		--arg owner "$(OWNER_NAME)" \
		'{
			name: $$name,
			owner: {name: $$owner},
			plugins: unique_by(.name) | map({
				name: .name,
				source: "./plugins/" + .name
			})
		}' \
		$(filter-out Makefile,$^) </dev/null > "$@"

.github/plugin/marketplace.json: $(PLUGIN_MANIFESTS) $(wildcard plugins/*/.github/plugin/plugin.json) Makefile
	@jq -s \
		--arg name "$(MARKETPLACE_NAME)" \
		--arg owner "$(OWNER_NAME)" \
		'{
			name: $$name,
			owner: {name: $$owner},
			plugins: unique_by(.name) | map({
				name: .name,
				source: "./plugins/" + .name
			})
		}' \
		$(filter-out Makefile,$^) </dev/null > "$@"
