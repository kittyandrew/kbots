{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    dream2nix.url = "github:nix-community/dream2nix";
  };
  outputs = {
    nixpkgs,
    dream2nix,
    ...
  }: let
    systems = ["x86_64-linux"];
    forEachSystem = fn:
      nixpkgs.lib.genAttrs systems (system:
        fn {
          pkgs = nixpkgs.legacyPackages.${system};
        });
  in {
    packages = forEachSystem ({pkgs}: let
      py-vtraty-pes-bot = dream2nix.lib.evalModules {
        packageSets.nixpkgs = pkgs;
        modules = [
          ./default.nix
          {
            paths.projectRoot = ./.;
            paths.projectRootFile = "flake.nix";
            paths.package = ./.;
          }
        ];
      };
    in {
      vtraty-pes-bot = py-vtraty-pes-bot;
      docker-image = pkgs.dockerTools.buildImage {
        name = "vtraty-pes-bot";
        tag = "latest";
        copyToRoot = pkgs.buildEnv {
          name = "image-root";
          paths = [
            pkgs.wkhtmltopdf
            pkgs.which # imgkit depends on this to find wkhtmltoimage ...
            pkgs.ffmpeg
          ];
          pathsToLink = ["/bin"];
        };
        config = {
          WorkingDir = "/usr/src/app";
          Entrypoint = ["${pkgs.lib.getExe' py-vtraty-pes-bot "vtraty-pes-bot"}"];
          # @NOTE: SENTRY_DSN is passed at runtime via .env/docker-compose, not baked in.
          #   GIT_SHA requires `--impure` to read the env var at build time (nix is pure by default).
          #   Without --impure, GIT_SHA resolves to "" and release tracking becomes a no-op.
          Env = [
            "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            "TMPDIR=/tmp"
            "SENTRY_ENVIRONMENT=production"
            "GIT_SHA=${builtins.getEnv "GIT_SHA"}"
          ];
        };
        # @NOTE: Created dirs here are not actually `/tmp`, but `tmp`, because we are creating
        #  a dir "in some nix sandbox", which only later will become docker image root (`/`).
        extraCommands = ''
          mkdir -m 0777 tmp  # creating /tmp since seemingly `wkhtmltoimage` requires it.
          mkdir -m 0777 .cache  # creating /.cache since seemingly `wkhtmltoimage` requires it.
        '';
      };
    });

    devShells = forEachSystem ({pkgs}: let
      libs = with pkgs; [
        stdenv.cc.cc
        zlib
        glib
        libGL
      ];
      py-vtraty-pes-bot = dream2nix.lib.evalModules {
        packageSets.nixpkgs = pkgs;
        modules = [
          ./default.nix
          {
            paths.projectRoot = ./.;
            paths.projectRootFile = "flake.nix";
            paths.package = ./.;
          }
        ];
      };
    in {
      default = pkgs.mkShell {
        inputsFrom = [py-vtraty-pes-bot.devShell];
        buildInputs = [
          py-vtraty-pes-bot.config.deps.python.pkgs.mypy
          pkgs.actionlint
          pkgs.alejandra
          pkgs.wkhtmltopdf
          pkgs.ffmpeg
          pkgs.poetry
          pkgs.ruff
          pkgs.zizmor
        ];
        # Upon installation we need to do additional configurations.
        shellHook = ''
          # Some python packages do RUNTIME DL loading from the provided paths, sigh.
          export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath libs}

          echo -e "\nWelcome to the shell :)\n"
        '';
      };
    });
  };
}
