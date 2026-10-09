{
  description = "Telegram bots for Vtraty";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };

    # Pinned by commit on upstream's v2 branch: V2 has no git tags. No nixpkgs.follows - upstream's nix/hashes.json
    # matches only its own nixpkgs and bun, so following ours breaks the node_modules hash.
    opencode.url = "github:anomalyco/opencode/229b422bd600df2a2b8d637fec5653dff779827f";
  };
  outputs = {
    self,
    nixpkgs,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
    opencode,
    ...
  }: let
    inherit (nixpkgs) lib;
    systems = ["x86_64-linux"];
    forEachSystem = fn:
      lib.genAttrs systems (system:
        fn {
          inherit system;
          pkgs = nixpkgs.legacyPackages.${system};
        });

    workspace = uv2nix.lib.workspace.loadWorkspace {workspaceRoot = ./.;};
    editableOverlay = workspace.mkEditablePyprojectOverlay {root = "$REPO_ROOT";};
    revision = self.rev or self.dirtyRev or "dirty";
    created = "1970-01-01T00:00:01Z";
    user = "65532:65532";
    shared = import ./nix/shared {
      inherit lib pyproject-build-systems pyproject-nix workspace created revision user;
    };
    inherit (shared) mkEnv mkImage mkPythonSet mkWkhtmltox;

    pythonSets = forEachSystem ({pkgs, ...}: {
      admin = mkPythonSet pkgs {vtraty-admin-bot = [];};
      dev = mkPythonSet pkgs workspace.deps.all;
      pes = mkPythonSet pkgs {vtraty-pes-bot = [];};
    });
  in {
    packages = forEachSystem ({
      pkgs,
      system,
    }: let
      pythonSet = pythonSets.${system}.pes;
      adminPythonSet = pythonSets.${system}.admin;
      pes = mkEnv pythonSet "pes-env" {vtraty-pes-bot = [];} "vtraty-pes-bot";
      admin = mkEnv adminPythonSet "admin-env" {vtraty-admin-bot = [];} "vtraty-admin-bot";
      wkhtmltox = mkWkhtmltox pkgs;
      opencodeServer = opencode.packages.${system}.opencode.overrideAttrs {
        postPatch = ""; # upstream's downgrades packages/script's Bun range check to a warning; a mismatch must fail
      };
      # The fact-check sidecar: agent and plugin come from pes/opencode, all state from one directory, so neither
      # the image nor a local run picks up a host ~/.claude, ~/.agents or ~/.config/opencode.
      pes-opencode = pkgs.writeShellApplication {
        name = "pes-opencode";
        runtimeInputs = [pkgs.coreutils];
        text = ''
          : "''${PES_OPENCODE_STATE:?set to the sidecar state directory}"
          : "''${OPENCODE_SERVER_PASSWORD:?set the password kbots uses for Basic auth}"
          : "''${KBOTS_CALLBACK_URL:?set to the PES bot callback server, e.g. http://127.0.0.1:8765}"
          : "''${KBOTS_CALLBACK_TOKEN:?set to the PES bot factcheck callback_token}"
          PES_OPENCODE_STATE=$(realpath -m "$PES_OPENCODE_STATE") # local runs pass a relative path; keep HOME and XDG absolute
          export HOME="$PES_OPENCODE_STATE/home" XDG_DATA_HOME="$PES_OPENCODE_STATE/data" # data: the DB, ChatGPT login included
          export XDG_CACHE_HOME="$PES_OPENCODE_STATE/cache" XDG_STATE_HOME="$PES_OPENCODE_STATE/state"
          export OPENCODE_CONFIG_DIR=${./pes/opencode} OPENCODE_DISABLE_PROJECT_CONFIG=1 OPENCODE_DISABLE_FILEWATCHER=1
          # Failed turns are logged only by opencode ("Failed to drain Session"); else they land in a file in the state dir.
          export OPENCODE_PRINT_LOGS=1 OPENCODE_LOG_LEVEL=WARN
          exec ${lib.getExe opencodeServer} serve "$@"
        '';
      };
    in {
      inherit pes admin pes-opencode;
      default = pes;

      pes-image =
        mkImage pkgs "vtraty-pes-bot" pes "vtraty-pes-bot" [
          pkgs.freefont_ttf
          pkgs.which
          wkhtmltox
        ] [
          "FONTCONFIG_FILE=${wkhtmltox.fontconfig.out}/etc/fonts/fonts.conf"
        ];
      admin-image = mkImage pkgs "vtraty-admin-bot" admin "vtraty-admin-bot" [] [];
      pes-opencode-image = mkImage pkgs "vtraty-pes-opencode" pes-opencode "pes-opencode" [] ["PES_OPENCODE_STATE=/usr/src/app/data"];
    });

    apps = forEachSystem ({system, ...}: let
      packages = self.packages.${system};
    in {
      pes = {
        type = "app";
        meta.description = "Vtraty pes Telegram bot";
        program = "${lib.getExe' packages.pes "vtraty-pes-bot"}";
      };
      admin = {
        type = "app";
        meta.description = "Admin Telegram bot";
        program = "${lib.getExe' packages.admin "vtraty-admin-bot"}";
      };
      default = self.apps.${system}.pes;
    });

    devShells = forEachSystem ({
      pkgs,
      system,
    }: let
      pythonSet = pythonSets.${system}.dev.overrideScope editableOverlay;
      virtualenv = pythonSet.mkVirtualEnv "kbots-dev-env" {
        vtraty-admin-bot = [];
        vtraty-pes-bot = [];
      };
    in {
      default = pkgs.mkShell {
        packages = [
          virtualenv
          pkgs.actionlint
          pkgs.alejandra
          pkgs.deadnix
          pkgs.ffmpeg
          pkgs.mypy
          pkgs.ruff
          pkgs.uv
          pkgs.wkhtmltopdf
          pkgs.zizmor
        ];
        env = {
          UV_NO_SYNC = "1";
          UV_PYTHON = pythonSet.python.interpreter;
          UV_PYTHON_DOWNLOADS = "never";
        };
        shellHook = ''
          unset PYTHONPATH
          export REPO_ROOT=$(git rev-parse --show-toplevel)

          echo -e "\nWelcome to the shell :)\n"
        '';
      };
    });

    formatter = forEachSystem ({pkgs, ...}: pkgs.alejandra);
  };
}
