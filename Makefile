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

.agents/plugins/marketplace.json: $(PLUGIN_MANIFESTS) Makefile
	plugins=$$(jq -s '
		map({
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
	' $(PLUGIN_MANIFESTS))
	jq -n \
		--arg name "$(MARKETPLACE_NAME)" \
		--argjson plugins "$$plugins" \
		'{name: $$name, plugins: $$plugins}' > "$@"

.github/plugin/marketplace.json: $(PLUGIN_MANIFESTS) Makefile
	plugins=$$(jq -s '
		map({
			name: .name,
			source: "./plugins/" + .name
		})
	' $(PLUGIN_MANIFESTS))
	jq -n \
		--arg name "$(MARKETPLACE_NAME)" \
		--arg owner "$(OWNER_NAME)" \
		--argjson plugins "$$plugins" \
		'{name: $$name, owner: {name: $$owner}, plugins: $$plugins}' > "$@"

.claude-plugin/marketplace.json: $(PLUGIN_MANIFESTS) Makefile
	plugins=$$(jq -s '
		map({
			name: .name,
			source: "./plugins/" + .name
		})
	' $(PLUGIN_MANIFESTS))
	jq -n \
		--arg name "$(MARKETPLACE_NAME)" \
		--arg owner "$(OWNER_NAME)" \
		--argjson plugins "$$plugins" \
		'{name: $$name, owner: {name: $$owner}, plugins: $$plugins}' > "$@"
