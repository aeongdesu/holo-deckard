variable "BUILDID" { default = "" }
variable "VERSION" { default = "" }
variable "IMAGE_BRANCH" { default = "" }
variable "GITSHA" { default = "" }
variable "STEAMVR_VERSION" { default = "" }
variable "SOURCE" { default = "" }
variable "REVISION" { default = "" }
variable "CREATED" { default = "" }

variable "BASE_TAGS" { default = "holo-deckard:base" }
variable "BASE_DEVEL_TAGS" { default = "holo-deckard:base-devel" }
variable "FULL_TAGS" { default = "holo-deckard:full" }

function "csv" {
  params = [s]
  result = compact(split(",", s))
}

group "default" {
  targets = ["base", "base-devel", "full"]
}

target "_common" {
  context    = "."
  dockerfile = "Dockerfile"
  platforms  = ["linux/arm64"]
  labels = {
    "org.opencontainers.image.description" = "Unofficial container image of the SteamOS (holo) userland for Steam Frame (deckard)"
    "org.opencontainers.image.source"      = SOURCE
    "org.opencontainers.image.revision"    = REVISION
    "org.opencontainers.image.created"     = CREATED
    "org.opencontainers.image.version"     = BUILDID
    "org.opencontainers.image.licenses"    = "NOASSERTION"
    "holo-deckard.buildid"                 = BUILDID
    "holo-deckard.version"                 = VERSION
    "holo-deckard.branch"                  = IMAGE_BRANCH
    "holo-deckard.gitsha"                  = GITSHA
    "holo-deckard.steamvr-version"         = STEAMVR_VERSION
  }
}

target "base" {
  inherits = ["_common"]
  target   = "base"
  tags     = csv(BASE_TAGS)
  labels   = { "org.opencontainers.image.title" = "holo-deckard base" }
}

target "base-devel" {
  inherits = ["_common"]
  target   = "base-devel"
  tags     = csv(BASE_DEVEL_TAGS)
  labels   = { "org.opencontainers.image.title" = "holo-deckard base-devel" }
}

target "full" {
  inherits = ["_common"]
  target   = "full"
  tags     = csv(FULL_TAGS)
  labels   = { "org.opencontainers.image.title" = "holo-deckard full" }
}
