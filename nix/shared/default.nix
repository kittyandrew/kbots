{
  lib,
  pyproject-build-systems,
  pyproject-nix,
  workspace,
  created,
  revision,
  user,
}: let
  mkOverlay = dependencies:
    workspace.mkPyprojectOverlay {
      inherit dependencies;
      sourcePreference = "wheel";
    };
in {
  mkWkhtmltox = pkgs: let
    baseFontconfig = pkgs.fontconfig.override {dejavu_fonts = {minimal = pkgs.freefont_ttf;};};
    fontconfig = baseFontconfig.overrideAttrs (_old: {
      doCheck = false;
      doInstallCheck = false;
    });
  in
    pkgs.stdenvNoCC.mkDerivation {
      pname = "wkhtmltox-bin";
      version = "0.12.6.1-3";

      src = pkgs.fetchurl {
        url = "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb";
        hash = "sha256-mLoNFXtQ028jvQ3t9MCqKMewxQ/NzcVKpba7uoGjlB0=";
      };

      dontUnpack = true;
      nativeBuildInputs = with pkgs; [autoPatchelfHook dpkg];
      buildInputs = with pkgs; [
        fontconfig
        freetype
        glibc
        libjpeg.out
        libpng
        openssl
        stdenv.cc.cc.lib
        libx11
        libxrender
        zlib
      ];

      passthru = {inherit fontconfig;};

      installPhase = ''
        runHook preInstall

        mkdir -p unpacked "$out/bin"
        dpkg-deb -x "$src" unpacked
        cp unpacked/usr/local/bin/wkhtmltoimage unpacked/usr/local/bin/wkhtmltopdf "$out/bin/"

        runHook postInstall
      '';
    };

  mkPythonSet = pkgs: dependencies: let
    python = pkgs.python313;
    manylinuxBase = with pkgs; [
      glibc
      stdenv.cc.cc.lib
      zlib
    ];
  in
    (pkgs.callPackage pyproject-nix.build.packages {inherit python;}).overrideScope (
      lib.composeManyExtensions [
        pyproject-build-systems.overlays.wheel
        (_final: _prev: {
          pythonManylinuxPackages = {
            manylinux1 = manylinuxBase;
            manylinux2010 = manylinuxBase;
            manylinux2014 = manylinuxBase;
          };
        })
        (mkOverlay dependencies)
        (final: prev:
          lib.optionalAttrs (prev ? pyaes) {
            pyaes = prev.pyaes.overrideAttrs (old: {
              nativeBuildInputs = (old.nativeBuildInputs or []) ++ final.resolveBuildSystem {setuptools = [];};
            });
          }
          // lib.optionalAttrs (prev ? cryptg) {
            cryptg = prev.cryptg.overrideAttrs (_old: {
              buildInputs = with pkgs; [
                glibc
                stdenv.cc.cc.lib
              ];
            });
          }
          // lib.optionalAttrs (prev ? opencv-python-headless) {
            opencv-python-headless = prev.opencv-python-headless.overrideAttrs (old: {
              buildInputs = (old.buildInputs or []) ++ [pkgs.zlib];
            });
          })
      ]
    );

  mkEnv = pythonSet: name: spec: mainProgram:
    (pythonSet.mkVirtualEnv name spec).overrideAttrs (old: {
      meta = (old.meta or {}) // {inherit mainProgram;};
    });

  mkImage = pkgs: name: env: mainProgram: extraPaths: extraEnv:
    pkgs.dockerTools.buildLayeredImage {
      inherit name created;
      tag = "latest";
      contents =
        [
          env
          pkgs.cacert
          pkgs.iana-etc
        ]
        ++ extraPaths;
      config = {
        Entrypoint = ["${lib.getExe' env mainProgram}"];
        WorkingDir = "/usr/src/app";
        User = user;
        Env =
          [
            "PATH=/bin"
            "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            "TMPDIR=/tmp"
            "HOME=/home/kbots"
            "SENTRY_ENVIRONMENT=production"
            "SENTRY_RELEASE=${revision}"
          ]
          ++ extraEnv;
        Labels = {
          "org.opencontainers.image.created" = created;
          "org.opencontainers.image.revision" = revision;
          "org.opencontainers.image.source" = "https://github.com/kittyandrew/kbots";
        };
      };
      extraCommands = ''
        mkdir -m 0777 -p tmp usr/src/app/data home/kbots home/kbots/.cache
      '';
      meta.mainProgram = mainProgram;
    };
}
