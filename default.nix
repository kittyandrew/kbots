{
  config,
  lib,
  dream2nix,
  ...
}: let
  pyproject = lib.importTOML (config.mkDerivation.src + /pyproject.toml);
in {
  imports = [dream2nix.modules.dream2nix.pip];

  deps = {nixpkgs, ...}: {
    python = nixpkgs.python3;
  };

  inherit (pyproject.project) name version;

  mkDerivation = {
    src = lib.cleanSourceWith {
      src = lib.cleanSource ./.;
      filter = name: type:
        !(builtins.any (x: x) [
          (lib.hasSuffix ".nix" name)
          (lib.hasPrefix "." (builtins.baseNameOf name) && !(lib.hasPrefix ".github" (builtins.baseNameOf name)))
          (lib.hasSuffix "flake.lock" name)
        ]);
    };
    meta.mainProgram = "vtraty-pes-bot";
  };

  buildPythonPackage = {
    pyproject = true;
    pythonImportsCheck = ["vtraty_pes_bot"];
  };

  pip = let
    # helper: given a build backend derivation and a list of package names,
    # build `overrides.<name> = { buildPythonPackage.pyproject = true;
    #                             buildPythonPackage."build-system" = [ backend ]; }`
    mkOverridesFor = backend: names:
      lib.genAttrs names (_: {
        buildPythonPackage.pyproject = true;
        buildPythonPackage.build-system = [backend];
      });

    setuptoolsPkgs = [
      "pyaes"
      "tgcrypto"
    ];
  in {
    requirementsList =
      pyproject.build-system.requires or []
      ++ pyproject.project.dependencies or [];
    flattenDependencies = true;

    # compose the final overrides set for packages by unioning groups
    overrides = mkOverridesFor config.deps.python.pkgs.setuptools setuptoolsPkgs;
  };
}
