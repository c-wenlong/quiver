{
  description = "quiver: one command to launch, resume, and analyze many AI coding agents";

  # Pinned to the same release branch the consumer dotfiles use, so a
  # `follows` there dedupes the package set instead of silently building
  # against a different Python.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-26.05-darwin";

  outputs = { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAllSystems = f: lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in
    {
      packages = forAllSystems (pkgs: rec {
        default = quiver;

        quiver = pkgs.python3Packages.buildPythonApplication {
          pname = "quiver";
          # One source of truth: the wheel and the derivation cannot drift.
          version = (lib.importTOML ./pyproject.toml).project.version;
          pyproject = true;

          # Only these paths affect the build hash. Editing docs, examples or
          # CHANGELOG.md does not trigger a rebuild.
          src = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./pyproject.toml
              ./README.md   # pyproject declares readme = "README.md"
              ./src
              ./tests
            ];
          };

          build-system = with pkgs.python3Packages; [ hatchling ];

          # The core CLI is stdlib-only. tomli backfills tomllib on 3.10.
          dependencies = lib.optionals (pkgs.python3.pythonOlder "3.11")
            [ pkgs.python3Packages.tomli ];

          # The suite runs under unittest, not pytest: tests/conftest.py is a
          # pytest-only fixture and is never imported here. Every command
          # reads ~/.quiver, so the check gets a throwaway HOME like CI does.
          # One test inits a git repo to find a project root.
          nativeCheckInputs = [ pkgs.git ];
          checkPhase = ''
            runHook preCheck
            HOME="$(mktemp -d)" python -m unittest discover -s tests -p 'test_*.py'
            runHook postCheck
          '';
          pythonImportsCheck = [ "quiver" ];

          meta = {
            description = "One command to launch, resume, and analyze many AI coding agents";
            homepage = "https://github.com/c-wenlong/quiver";
            license = lib.licenses.mit;
            mainProgram = "swe";
            platforms = lib.platforms.unix;
          };
        };
      });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        };
      });

      # `nix flake check` builds the package, which runs the test suite.
      checks = forAllSystems (pkgs: {
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.hatchling ps.coverage ]))
          ];
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
          '';
        };
      });
    };
}
