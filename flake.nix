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
  };
  outputs = {
    self,
    nixpkgs,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
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
    in {
      inherit pes admin;
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
    });

    apps = forEachSystem ({system, ...}: let
      packages = self.packages.${system};
    in {
      pes = {
        type = "app";
        program = "${lib.getExe' packages.pes "vtraty-pes-bot"}";
      };
      admin = {
        type = "app";
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
