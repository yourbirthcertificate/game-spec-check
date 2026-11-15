"""Discovery providers against synthetic launcher records, plus identity and dedup."""
import gzip
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from specbench import discovery, identity, library  # noqa: E402
from specbench.discovery import generic, launchers, model  # noqa: E402
from specbench.discovery.env import Env  # noqa: E402

UNINSTALL = r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"


def touch(path, data=b""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def make_lnk(path, target):
    """A minimal Shell Link file with LinkInfo and a Unicode local base path."""
    header = bytearray(0x4C)
    header[0:4] = b"\x4c\x00\x00\x00"
    struct.pack_into("<I", header, 0x14, 0x02)               # HasLinkInfo
    uni = target.encode("utf-16-le") + b"\x00\x00"
    ansi = b"\x00"
    li_header = 0x24
    off_ansi = li_header
    off_suffix = off_ansi + len(ansi)
    off_uni = off_suffix + 1
    size = off_uni + len(uni)
    info = struct.pack("<IIIIIIIII", size, li_header, 1, 0, off_ansi, 0, off_suffix, off_uni, 0)
    info = info[:li_header] + ansi + b"\x00" + uni
    touch(path, bytes(header) + info)


class Ctx(discovery.ScanContext):
    pass


class Providers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = Ctx()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    def fenv(self, registry=None, drives=()):
        """An Env that sees only this test's temp folders, never the real PC."""
        return Env(registry=registry or {}, drives=list(drives),
                   environ={"ProgramData": self.p("PD"), "ProgramFiles": self.p("PF"),
                            "ProgramFiles(x86)": self.p("PF86"), "APPDATA": self.p("AppData")})

    def test_steam_libraries_and_bad_manifests(self):
        root, lib2 = self.p("Steam"), self.p("Lib2")
        touch(os.path.join(root, "steamapps", "libraryfolders.vdf"),
              '"libraryfolders"\n{\n "1" { "path" "%s" }\n}' % lib2.replace("\\", "\\\\"))
        touch(os.path.join(root, "steamapps", "appmanifest_10.acf"),
              '"AppState" { "appid" "10" "name" "Counter-Strike" "installdir" "cs" "SizeOnDisk" "1073741824" }')
        os.makedirs(os.path.join(root, "steamapps", "common", "cs"))
        touch(os.path.join(lib2, "steamapps", "appmanifest_228980.acf"),
              '"AppState" { "appid" "228980" "name" "Steamworks Common Redistributables" }')
        touch(os.path.join(lib2, "steamapps", "appmanifest_20.acf"),
              '"AppState" { "appid" "20" "name" "Moved Game" "installdir" "gone" }')
        touch(os.path.join(lib2, "steamapps", "appmanifest_30.acf"), "garbage")
        env = self.fenv({r"HKCU\Software\Valve\Steam": {"SteamPath": root}})
        res = launchers.scan_steam(env, self.ctx)
        by_id = {i["provider_id"]: i for i in res["installs"]}
        self.assertEqual(set(by_id), {"10", "20"})
        self.assertTrue(by_id["10"]["present"])
        self.assertFalse(by_id["20"]["present"])
        self.assertEqual(by_id["10"]["size_gb"], 1.0)
        self.assertEqual(by_id["10"]["launch"]["target"], "steam://rungameid/10")
        self.assertIn("could not be read", res["message"])

    def test_steam_not_installed(self):
        res = launchers.scan_steam(self.fenv(), self.ctx)
        self.assertEqual(res["status"], "not_installed")

    def test_steam_game_folder_without_manifest(self):
        root = self.p("Steam")
        common = os.path.join(root, "steamapps", "common")
        touch(os.path.join(root, "steamapps", "appmanifest_10.acf"),
              '"AppState" { "appid" "10" "name" "Listed" "installdir" "listed" }')
        touch(os.path.join(common, "listed", "listed.exe"))
        touch(os.path.join(common, "Forgotten", "forgotten.exe"))
        touch(os.path.join(common, "Forgotten", "steam_appid.txt"), "4000\n")
        touch(os.path.join(common, "Leftover", "saves", "slot1.sav"))      # no exe: uninstall leftovers
        touch(os.path.join(common, "Steamworks Shared", "x.exe"))
        res = launchers.scan_steam(self.fenv({r"HKCU\Software\Valve\Steam": {"SteamPath": root}}), self.ctx)
        by = {i["title"]: i for i in res["installs"]}
        self.assertEqual(sorted(by), ["Forgotten", "Listed"])
        self.assertEqual((by["Forgotten"]["confidence"], by["Forgotten"]["ids"]), ("medium", {"steam": "4000"}))
        self.assertIsNone(by["Forgotten"]["launch"])
        self.assertIn("Steam has no record of it", res["message"])
        # Older manifests store an escaped path relative to the library.
        touch(os.path.join(root, "steamapps", "appmanifest_390220.acf"),
              '"AppState" { "appid" "390220" "name" "Toilet Tycoon" '
              '"installdir" "SteamApps\\\\\\\\common\\\\\\\\Toilet Tycoon\\\\\\\\" }')
        touch(os.path.join(common, "Toilet Tycoon", "game.exe"))
        res = launchers.scan_steam(self.fenv({r"HKCU\Software\Valve\Steam": {"SteamPath": root}}), self.ctx)
        tt = [i for i in res["installs"] if i["title"] == "Toilet Tycoon"]
        self.assertEqual([(i["confidence"], i["present"]) for i in tt], [("high", True)])
        # A library on another drive is found even with Steam itself gone.
        drive = self.p("E") + os.sep
        touch(os.path.join(drive, "SteamLibrary", "steamapps", "common", "Old", "old.exe"))
        res = launchers.scan_steam(self.fenv(drives=[drive]), self.ctx)
        self.assertEqual(([i["title"] for i in res["installs"]], res["status"]), (["Old"], "ok"))

    def test_steam_recovers_moved_game_without_replacing_present_install(self):
        root = self.p("Steam")
        common = os.path.join(root, "steamapps", "common")
        for appid, name, dirname in (("10", "Moved Game", "gone"),
                                     ("20", "Listed Game", "listed")):
            touch(os.path.join(root, "steamapps", "appmanifest_%s.acf" % appid),
                  '"AppState" { "appid" "%s" "name" "%s" "installdir" "%s" }' %
                  (appid, name, dirname))
        touch(os.path.join(common, "listed", "game.exe"))
        for dirname, appid in (("Moved", "10"), ("MovedCopy", "10"), ("ListedCopy", "20")):
            touch(os.path.join(common, dirname, "game.exe"))
            touch(os.path.join(common, dirname, "steam_appid.txt"), appid)
        env = self.fenv({r"HKCU\Software\Valve\Steam": {"SteamPath": root}})
        res = launchers.scan_steam(env, self.ctx)
        self.assertEqual(len(res["installs"]), 2)
        by_id = {i["provider_id"]: i for i in res["installs"]}
        recovered = by_id["10"]
        self.assertEqual(recovered["path"], os.path.join(common, "Moved"))
        self.assertTrue(recovered["present"])
        self.assertEqual(recovered["install_id"], model.install_id("steam", "10"))
        self.assertEqual(recovered["confidence"], "medium")
        self.assertIsNone(recovered["launch"])
        self.assertEqual((by_id["20"]["path"], by_id["20"]["confidence"]),
                         (os.path.join(common, "listed"), "high"))
        self.assertIn("1 game folder", res["message"])

    def test_epic_manifests(self):
        data = self.p("Epic")
        game_dir = self.p("Games", "Fortnite")
        os.makedirs(game_dir)
        man = os.path.join(data, "Manifests")
        touch(os.path.join(man, "a.item"), json.dumps({
            "DisplayName": "Fortnite", "AppName": "Fortnite", "InstallLocation": game_dir,
            "CatalogNamespace": "fn", "CatalogItemId": "abc", "AppCategories": ["public", "games"],
            "InstallSize": 2 ** 31}))
        touch(os.path.join(man, "dlc.item"), json.dumps({
            "DisplayName": "Some DLC", "AppName": "dlc1", "MainGameAppName": "Fortnite"}))
        touch(os.path.join(man, "app.item"), json.dumps({
            "DisplayName": "Unreal Editor", "AppName": "UE_5", "AppCategories": ["engines"]}))
        touch(os.path.join(man, "half.item"), json.dumps({
            "DisplayName": "Half Installed", "AppName": "half", "bIsIncompleteInstall": True}))
        touch(os.path.join(man, "broken.item"), "{not json")
        env = self.fenv({r"HKLM\SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher": {"AppDataPath": data}})
        res = launchers.scan_epic(env, self.ctx)
        self.assertEqual([i["title"] for i in res["installs"]], ["Fortnite"])
        inst = res["installs"][0]
        self.assertEqual(inst["ids"], {"epic": "Fortnite"})
        self.assertIn("fn%3Aabc%3AFortnite", inst["launch"]["target"])
        self.assertIn("1 record", res["message"])
        self.assertEqual(launchers.scan_epic(self.fenv(), self.ctx)["status"], "not_installed")

    def epic_env(self, reg=True):
        return self.fenv({r"HKLM\SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher":
                          {"AppDataPath": self.p("PD", "Epic", "EpicGamesLauncher", "Data")}} if reg else {})

    def test_epic_launcher_without_records_is_not_called_missing(self):
        os.makedirs(self.p("PD", "Epic", "EpicGamesLauncher", "Data"))
        res = launchers.scan_epic(self.epic_env(), self.ctx)
        self.assertEqual((res["status"], res["installs"]), ("ok", []))
        self.assertIn("lists no installed games", res["message"])

    def test_epic_game_folder_the_launcher_forgot(self):
        lib = self.p("PF", "Epic Games")
        touch(os.path.join(lib, "Fortnite", ".egstore", "D361.manifest"), b"\x0c\xc0\xbe\x44")
        touch(os.path.join(lib, "Launcher", "Portal", "x.exe"))
        touch(os.path.join(lib, "OtherGame", ".egstore", "a.mancpn"),
              json.dumps({"AppName": "Other", "CatalogNamespace": "ns", "CatalogItemId": "cat"}))
        os.makedirs(os.path.join(lib, "NotAGame"))
        res = launchers.scan_epic(self.epic_env(), self.ctx)
        by = {i["title"]: i for i in res["installs"]}
        self.assertEqual(sorted(by), ["Fortnite", "OtherGame"])
        self.assertEqual((by["Fortnite"]["confidence"], by["Fortnite"]["launch"]), ("medium", None))
        self.assertIn("ns%3Acat%3AOther", by["OtherGame"]["launch"]["target"])
        self.assertIn("has no record of them", res["message"])
        # Without the launcher's registry key the folders still count.
        self.assertEqual(launchers.scan_epic(self.epic_env(reg=False), self.ctx)["status"], "ok")

    def test_epic_install_list_and_no_duplicates(self):
        game = self.p("PF", "Epic Games", "Hades")
        touch(os.path.join(game, ".egstore", "x.manifest"))
        touch(self.p("PD", "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat"), json.dumps(
            {"InstallationList": [{"InstallLocation": game, "AppName": "Min", "NamespaceId": "n",
                                   "ItemId": "i"}]}))
        res = launchers.scan_epic(self.epic_env(), self.ctx)
        self.assertEqual([(i["title"], i["confidence"]) for i in res["installs"]], [("Hades", "high")])
        self.assertIn("n%3Ai%3AMin", res["installs"][0]["launch"]["target"])

    def test_epic_fallbacks_preserve_manifest_exclusions(self):
        lib = self.p("PF", "Epic Games")
        manifests = self.p("PD", "Epic", "EpicGamesLauncher", "Data", "Manifests")
        entries = []
        for name, flags in (("HalfInstalled", {"bIsIncompleteInstall": True}),
                            ("UE_5", {"AppCategories": ["engines"]}),
                            ("Bonus", {"MainGameAppName": "BaseGame"}),
                            ("Music", {"DisplayName": "Game Soundtrack"})):
            path = os.path.join(lib, name)
            touch(os.path.join(path, ".egstore", "game.manifest"))
            manifest = {"DisplayName": name, "AppName": name, "InstallLocation": path}
            manifest.update(flags)
            touch(os.path.join(manifests, name + ".item"), json.dumps(manifest))
            entries.append({"InstallLocation": path, "AppName": name})
        # Check the folder fallback alone, and then both fallbacks together.
        for install_list in (False, True):
            with self.subTest(install_list=install_list):
                if install_list:
                    touch(self.p("PD", "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat"),
                          json.dumps({"InstallationList": entries}))
                res = launchers.scan_epic(self.epic_env(), self.ctx)
                self.assertEqual(res["installs"], [])
                self.assertEqual(res["status"], "ok")

    def test_epic_excluded_dlc_does_not_hide_base_game_in_same_folder(self):
        path = self.p("PF", "Epic Games", "BaseGame")
        touch(os.path.join(path, ".egstore", "game.manifest"))
        manifests = self.p("PD", "Epic", "EpicGamesLauncher", "Data", "Manifests")
        for filename, app, main in (("a_dlc.item", "DLC", "BaseGame"),
                                    ("b_base.item", "BaseGame", "BaseGame")):
            touch(os.path.join(manifests, filename), json.dumps({
                "DisplayName": app, "AppName": app, "MainGameAppName": main,
                "InstallLocation": path}))
        res = launchers.scan_epic(self.epic_env(), self.ctx)
        self.assertEqual([(i["provider_id"], i["confidence"]) for i in res["installs"]],
                         [("BaseGame", "high")])

    def test_gog_registry(self):
        game = self.p("GOG", "Witcher")
        os.makedirs(game)
        reg = {
            r"HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\1207658924": {
                "gameName": "The Witcher: Enhanced Edition", "path": game, "gameID": "1207658924",
                "exe": os.path.join(game, "witcher.exe"), "workingDir": game},
            r"HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\2": {"gameName": "A DLC", "path": game,
                                                          "dependsOn": "1207658924"},
        }
        res = launchers.scan_gog(self.fenv(reg), self.ctx)
        self.assertEqual(len(res["installs"]), 1)
        inst = res["installs"][0]
        self.assertEqual((inst["ids"], inst["edition"]), ({"gog": "1207658924"}, "Enhanced Edition"))
        self.assertEqual(inst["launch"]["kind"], "exe")

    def test_gog_folders_without_registry(self):
        lib = self.p("PF86", "GOG Galaxy", "Games")
        game = os.path.join(lib, "Stardew")
        touch(os.path.join(game, "Stardew Valley.exe"))
        touch(os.path.join(game, "goggame-1453375253.info"), json.dumps({
            "gameId": "1453375253", "rootGameId": "1453375253", "name": "Stardew Valley",
            "playTasks": [{"type": "FileTask", "isPrimary": True, "path": "Stardew Valley.exe"}]}))
        touch(os.path.join(game, "goggame-999.info"), json.dumps({
            "gameId": "999", "rootGameId": "1453375253", "name": "A DLC"}))
        touch(os.path.join(lib, "Evil", "goggame-1.info"), json.dumps({
            "gameId": "1", "name": "Evil", "playTasks": [{"type": "FileTask", "path": "..\\..\\x.exe"}]}))
        res = launchers.scan_gog(self.fenv(), self.ctx)
        by = {i["title"]: i for i in res["installs"]}
        self.assertEqual(sorted(by), ["Evil", "Stardew Valley"])
        sv = by["Stardew Valley"]
        self.assertEqual((sv["confidence"], sv["ids"]), ("medium", {"gog": "1453375253"}))
        self.assertTrue(sv["launch"]["target"].endswith("Stardew Valley.exe"))
        self.assertIsNone(by["Evil"]["launch"])          # a play task outside the folder is ignored
        self.assertIn("GOG has no record of them", res["message"])
        self.assertEqual(launchers.scan_gog(self.fenv(), self.ctx)["status"], "ok")
        shutil.rmtree(lib)
        self.assertEqual(launchers.scan_gog(self.fenv(), self.ctx)["status"], "not_installed")

    def test_gog_recovers_moved_game_without_replacing_present_install(self):
        lib = self.p("PF", "GOG Games")
        old_path = self.p("OldLibrary", "Moved")
        listed_path = os.path.join(lib, "Listed")
        touch(os.path.join(listed_path, "game.exe"))
        reg = {}
        for gid, path in (("10", old_path), ("20", listed_path)):
            reg[r"HKLM\SOFTWARE\WOW6432Node\GOG.com\Games" + "\\" + gid] = {
                "gameName": "Game " + gid, "gameID": gid, "path": path,
                "exe": os.path.join(path, "game.exe"), "workingDir": path}
        for dirname, gid in (("Moved", "10"), ("MovedCopy", "10"), ("ListedCopy", "20")):
            path = os.path.join(lib, dirname)
            touch(os.path.join(path, "game.exe"))
            touch(os.path.join(path, "goggame-%s.info" % gid), json.dumps({
                "gameId": gid, "name": "Game " + gid,
                "playTasks": [{"type": "FileTask", "isPrimary": True, "path": "game.exe"}]}))
        res = launchers.scan_gog(self.fenv(reg), self.ctx)
        self.assertEqual(len(res["installs"]), 2)
        by_id = {i["provider_id"]: i for i in res["installs"]}
        recovered = by_id["10"]
        new_path = os.path.join(lib, "Moved")
        self.assertEqual(recovered["path"], new_path)
        self.assertTrue(recovered["present"])
        self.assertEqual(recovered["install_id"], model.install_id("gog", "10"))
        self.assertEqual(recovered["confidence"], "medium")
        self.assertEqual(recovered["launch"],
                         {"kind": "exe", "target": os.path.join(new_path, "game.exe"), "cwd": new_path})
        self.assertEqual((by_id["20"]["path"], by_id["20"]["confidence"]), (listed_path, "high"))
        self.assertIn("1 game folder", res["message"])

    def test_unlisted_ubisoft_ea_battlenet_folders(self):
        touch(self.p("PF86", "Ubisoft", "Ubisoft Game Launcher", "games", "Far Cry 5", "uplay_install.state"))
        touch(self.p("PF", "EA Games", "Mass Effect", "__Installer", "installerdata.xml"),
              '<DiPManifest><gameTitles><gameTitle locale="en_US">Mass Effect Legendary Edition</gameTitle>'
              '</gameTitles></DiPManifest>')
        ow = self.p("PF86", "Overwatch")
        touch(os.path.join(ow, ".build.info"))
        touch(os.path.join(ow, ".product.db"))
        touch(self.p("PF86", "Half Product", ".build.info"))     # needs both files
        env = self.fenv()
        u = launchers.scan_ubisoft(env, self.ctx)
        self.assertEqual([(i["title"], i["confidence"]) for i in u["installs"]], [("Far Cry 5", "medium")])
        self.assertIn("Locate installed game", u["message"])
        e = launchers.scan_ea(env, self.ctx)
        self.assertEqual([i["title"] for i in e["installs"]], ["Mass Effect Legendary Edition"])
        b = launchers.scan_battlenet(env, self.ctx)
        self.assertEqual([i["title"] for i in b["installs"]], ["Overwatch"])
        self.assertEqual(b["status"], "ok")

    def test_ubisoft_ea_battlenet(self):
        ubi = self.p("Ubi", "Crew")
        ea = self.p("EA", "Sims")
        touch(os.path.join(ea, "__Installer", "installerdata.xml"), "<x/>")
        reg = {
            r"HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\5595": {"InstallDir": ubi},
            UNINSTALL + r"\Uplay Install 5595": {"DisplayName": "The Crew Motorfest"},
            UNINSTALL + r"\{sims}": {"DisplayName": "The Sims 4", "InstallLocation": ea,
                                    "Publisher": "Electronic Arts"},
            UNINSTALL + r"\Overwatch": {"DisplayName": "Overwatch", "InstallLocation": self.p("OW"),
                                        "UninstallString": '"C:\\ProgramData\\Battle.net\\Agent\\'
                                                           'Blizzard Uninstaller.exe" --uid=prometheus'},
            UNINSTALL + r"\Battle.net": {"DisplayName": "Battle.net", "UninstallString": "x"},
        }
        env = self.fenv(reg)
        u = launchers.scan_ubisoft(env, self.ctx)["installs"]
        self.assertEqual((u[0]["title"], u[0]["launch"]["target"]), ("The Crew Motorfest", "uplay://launch/5595/0"))
        e = launchers.scan_ea(env, self.ctx)["installs"]
        self.assertEqual([(i["title"], i["confidence"], i["launch"]) for i in e], [("The Sims 4", "high", None)])
        b = launchers.scan_battlenet(env, self.ctx)
        self.assertEqual([i["provider_id"] for i in b["installs"]], ["prometheus"])
        self.assertEqual(launchers.scan_battlenet(self.fenv(), self.ctx)["status"], "not_installed")

    def test_itch_database_and_receipts(self):
        appdata = self.p("AppData")
        dbdir = os.path.join(appdata, "itch", "db")
        os.makedirs(dbdir)
        con = sqlite3.connect(os.path.join(dbdir, "butler.db"))
        con.executescript(
            "CREATE TABLE games (id INTEGER, title TEXT, classification TEXT);"
            "CREATE TABLE caves (id TEXT, game_id INTEGER, install_folder_name TEXT, install_location_id TEXT);"
            "CREATE TABLE install_locations (id TEXT, path TEXT);"
            "INSERT INTO games VALUES (1, 'Celeste Classic', 'game'), (2, 'A Font Pack', 'assets');"
            "INSERT INTO caves VALUES ('c1', 1, 'celeste', 'L'), ('c2', 2, 'fonts', 'L');"
            "INSERT INTO install_locations VALUES ('L', '%s');" % self.p("itchapps").replace("'", "''"))
        con.commit()
        con.close()
        res = launchers.scan_itch(Env(environ={"APPDATA": appdata}), self.ctx)
        self.assertEqual([i["title"] for i in res["installs"]], ["Celeste Classic"])
        # A receipt in an install location the database does not list.
        extra = self.p("itchapps", "extra", ".itch", "receipt.json.gz")
        os.makedirs(os.path.dirname(extra))
        with gzip.open(extra, "wb") as fh:
            fh.write(json.dumps({"game": {"id": 9, "title": "Unlisted Jam Game", "classification": "game"}}).encode())
        res = launchers.scan_itch(Env(environ={"APPDATA": appdata}), self.ctx)
        self.assertEqual([(i["title"], i["confidence"]) for i in res["installs"]],
                         [("Celeste Classic", "high"), ("Unlisted Jam Game", "medium")])
        os.remove(os.path.join(dbdir, "butler.db"))
        receipt = os.path.join(appdata, "itch", "apps", "game1", ".itch", "receipt.json.gz")
        os.makedirs(os.path.dirname(receipt))
        with gzip.open(receipt, "wb") as fh:
            fh.write(json.dumps({"game": {"id": 7, "title": "Receipt Game", "classification": "game"}}).encode())
        touch(os.path.join(appdata, "itch", "apps", "bad", ".itch", "receipt.json.gz"), b"not gzip")
        res = launchers.scan_itch(Env(environ={"APPDATA": appdata}), self.ctx)
        self.assertEqual([i["title"] for i in res["installs"]], ["Receipt Game"])
        self.assertIn("1 record", res["message"])

    def test_xbox_gaming_root(self):
        drive = self.p("D") + os.sep
        os.makedirs(drive)
        touch(os.path.join(drive, ".GamingRoot"), b"RGBX\x01\x00\x00\x00" + "XboxGames\x00".encode("utf-16-le"))
        touch(os.path.join(drive, "XboxGames", "Starfield", "Content", "MicrosoftGame.config"),
              '<Game configVersion="1"><Identity Name="BethesdaSoftworks.ProjectGold" '
              'Publisher="CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"/>'
              '<ShellVisuals DefaultDisplayName="Starfield"/>'
              '<ExecutableList><Executable Name="Starfield.exe" Id="Game"/></ExecutableList></Game>')
        touch(os.path.join(drive, "XboxGames", "Broken", "Content", "MicrosoftGame.config"), "<Game")
        touch(self.p("PF", "ModifiableWindowsApps", "OldPass", "MicrosoftGame.config"),
              '<Game><Identity Name="Old.Pass" Publisher="CN=X"/><ShellVisuals DefaultDisplayName="Old Pass Game"/>'
              '<ExecutableList><Executable Id="Game"/></ExecutableList></Game>')
        res = launchers.scan_xbox(self.fenv(drives=[drive]), self.ctx)
        self.assertEqual(sorted(i["title"] for i in res["installs"]), ["Old Pass Game", "Starfield"])
        res["installs"] = [i for i in res["installs"] if i["title"] == "Starfield"]
        self.assertEqual(res["installs"][0]["launch"]["target"],
                         "shell:AppsFolder\\BethesdaSoftworks.ProjectGold_8wekyb3d8bbwe!Game")
        self.assertEqual(launchers.publisher_id(
            "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"), "8wekyb3d8bbwe")

    def test_programs_need_game_files_and_skip_apps(self):
        game, app, cef = self.p("Prog", "Hollow"), self.p("Prog", "Notepad"), self.p("Prog", "Peripheral")
        touch(os.path.join(game, "hollow.exe"))
        touch(os.path.join(game, "UnityPlayer.dll"))
        touch(os.path.join(app, "notepad.exe"))
        touch(os.path.join(cef, "tool.exe"))
        touch(os.path.join(cef, "chrome_100_percent.pak"))
        touch(os.path.join(cef, "steam_api64.dll"))
        reg = {UNINSTALL + r"\a": {"DisplayName": "Hollow Thing", "InstallLocation": game},
               UNINSTALL + r"\b": {"DisplayName": "Notepad Plus", "InstallLocation": app},
               UNINSTALL + r"\c": {"DisplayName": "Mouse Tool", "InstallLocation": cef},
               UNINSTALL + r"\d": {"DisplayName": "Microsoft Visual C++ 2015 Redistributable",
                                   "InstallLocation": game}}
        res = generic.scan_programs(Env(registry=reg), self.ctx)
        self.assertEqual([(i["title"], i["confidence"]) for i in res["installs"]], [("Hollow Thing", "low")])
        self.assertTrue(res["installs"][0]["launch"]["target"].endswith("hollow.exe"))

    def test_start_menu_shortcuts(self):
        game = self.p("G", "Indie")
        touch(os.path.join(game, "indie.exe"))
        touch(os.path.join(game, "GameAssembly.dll"))
        menu = self.p("PD", "Microsoft", "Windows", "Start Menu", "Programs")
        make_lnk(os.path.join(menu, "Indie Game.lnk"), os.path.join(game, "indie.exe"))
        make_lnk(os.path.join(menu, "Uninstall Indie.lnk"), os.path.join(game, "indie.exe"))
        self.assertEqual(model.lnk_target(os.path.join(menu, "Indie Game.lnk")), os.path.join(game, "indie.exe"))
        res = generic.scan_shortcuts(Env(environ={"ProgramData": self.p("PD")}), self.ctx)
        self.assertEqual([i["title"] for i in res["installs"]], ["Indie Game"])
        self.assertEqual(res["installs"][0]["launch"]["kind"], "shortcut")

    def test_folders_and_manual(self):
        top = self.p("MyGames")
        touch(os.path.join(top, "Cool Game", "cool.exe"))
        touch(os.path.join(top, "Cool Game", "Cool Game_Data", "globalgamemanagers"))
        touch(os.path.join(top, "Cool Game", "unins000.exe"))
        touch(os.path.join(top, "Random Tool", "tool.exe"))
        os.makedirs(os.path.join(top, "Empty"))
        res = generic.scan_folders([top, self.p("missing")], self.ctx)
        by = {i["title"]: i for i in res["installs"]}
        self.assertEqual(by["Cool Game"]["confidence"], "medium")
        self.assertTrue(by["Cool Game"]["launch"]["target"].endswith("cool.exe"))
        self.assertEqual(by["Random Tool"]["confidence"], "low")
        self.assertNotIn("Empty", by)
        self.assertIn("could not be read", res["message"])
        manual = generic.manual_installs([{"id": "abc", "exe": os.path.join(top, "Cool Game", "cool.exe"), "title": "Cool"},
                                          {"id": "def", "exe": self.p("nope.exe"), "title": "Gone"}])
        self.assertEqual([m["present"] for m in manual], [True, False])

    def test_scan_isolates_failures_and_cancels(self):
        def boom(env, ctx, s):
            raise RuntimeError("bad")
        old = discovery.PROVIDERS
        try:
            discovery.PROVIDERS = [("a", "A", boom),
                                   ("b", "B", lambda env, ctx, s: {"status": "ok", "message": "", "installs": []})]
            out = discovery.run_scan(Env(registry={}))
            self.assertEqual(out["a"]["status"], "error")
            self.assertEqual(out["b"]["status"], "ok")
            ctx = discovery.ScanContext()
            ctx.cancel()
            out = discovery.run_scan(Env(registry={}), ctx=ctx)
            self.assertEqual({v["status"] for v in out.values()}, {"cancelled"})
        finally:
            discovery.PROVIDERS = old

    def test_cancel_inside_provider(self):
        ctx = discovery.ScanContext()

        def slow(env, c, s):
            c.cancel()
            c.check()
        old = discovery.PROVIDERS
        try:
            discovery.PROVIDERS = [("a", "A", slow)]
            self.assertEqual(discovery.run_scan(Env(registry={}), ctx=ctx)["a"]["status"], "cancelled")
        finally:
            discovery.PROVIDERS = old


def inst(provider, title, pid=None, path=None, conf="high", ids=None):
    return model.make_install(provider, title, provider_id=pid, path=path, confidence=conf, ids=ids)


class Identity(unittest.TestCase):
    def test_title_keys_and_editions(self):
        self.assertEqual(model.title_key("The Witcher 3: Wild Hunt - Game of the Year Edition"),
                         model.title_key("The Witcher\u00ae 3: Wild Hunt"))
        self.assertEqual(model.title_key("DOOM Eternal Deluxe Edition"), model.title_key("DOOM Eternal"))
        self.assertNotEqual(model.title_key("Skyrim Special Edition"), model.title_key("Skyrim"))
        self.assertNotEqual(model.title_key("Age of Empires II: Definitive Edition"),
                            model.title_key("Age of Empires II"))
        self.assertEqual(model.split_edition("Edition"), ("Edition", None, False))

    def test_merge_across_launchers_keeps_copies(self):
        games = identity.merge([
            inst("steam", "The Witcher 3: Wild Hunt", "292030", ids={"steam": "292030"}),
            inst("gog", "The Witcher 3: Wild Hunt - Game of the Year Edition", "1495134320",
                 ids={"gog": "1495134320"}),
            inst("epic", "Skyrim Special Edition", "ss"),
            inst("steam", "The Elder Scrolls V: Skyrim", "72850", ids={"steam": "72850"}),
            inst("steam", "Skyrim Special Edition", "489830", ids={"steam": "489830"}),
        ])
        by = {g["title"]: g for g in games}
        w3 = by["The Witcher 3: Wild Hunt"]
        self.assertEqual(w3["providers"], ["gog", "steam"])
        self.assertEqual(w3["ids"], {"steam": "292030", "gog": "1495134320"})
        self.assertEqual(len(games), 3)
        sse = by["Skyrim Special Edition"]
        self.assertEqual(sse["providers"], ["epic", "steam"])
        self.assertEqual(sse["ids"]["steam"], "489830")

    def test_game_ids_are_stable(self):
        a = identity.merge([inst("epic", "Hades", "h")])[0]["game_id"]
        b = identity.merge([inst("gog", "Hades", "1")])[0]["game_id"]
        self.assertEqual(a, b)

    def test_review_and_dedup_of_heuristic_finds(self):
        tmp = tempfile.mkdtemp()
        try:
            steam_dir = os.path.join(tmp, "steam", "common", "Game")
            os.makedirs(steam_dir)
            other = os.path.join(tmp, "Other")
            os.makedirs(other)
            installs = [inst("steam", "Game", "1", steam_dir, ids={"steam": "1"}),
                        inst("programs", "Game", path=steam_dir, conf="low"),
                        inst("programs", "Other Game", path=other, conf="low"),
                        inst("shortcuts", "Other Game", path=other, conf="low")]
            lib, review = identity.visible_installs(installs, {})
            self.assertEqual([i["provider"] for i in lib], ["steam"])
            self.assertEqual([i["provider"] for i in review], ["programs"])
            lib, review = identity.visible_installs(installs, {review[0]["install_id"]: "accept"})
            self.assertEqual(len(lib), 2)
            lib, review = identity.visible_installs(installs, {installs[2]["install_id"]: "ignore",
                                                               installs[3]["install_id"]: "ignore"})
            self.assertEqual((len(lib), review), (1, []))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_and_removed_installs(self):
        lib = {"scan": {"installs": [inst("steam", "Old", "1"), inst("epic", "Kept", "k")]}}
        results = {"steam": {"status": "ok", "installs": [inst("steam", "New", "2")]},
                   "epic": {"status": "error", "message": "boom", "installs": []}}
        out = library.apply_scan(lib, results, now=1)
        titles = sorted(i["title"] for i in out["scan"]["installs"])
        self.assertEqual(titles, ["Kept", "New"])
        self.assertEqual(out["scan"]["providers"]["epic"]["kept"], 1)
        self.assertEqual(out["scan"]["providers"]["epic"]["status"], "error")

    def test_moved_game_keeps_its_id(self):
        a = inst("steam", "Game", "5", "C:\\Old")
        b = inst("steam", "Game", "5", "D:\\New")
        self.assertEqual(a["install_id"], b["install_id"])

    def test_resolve_order(self):
        game = identity.merge([inst("epic", "Hades II", "h2")])[0]
        calls = []

        def steam_search(term):
            calls.append(term)
            return [{"source": "steam", "id": "1145350", "name": "Hades II"},
                    {"source": "steam", "id": "1145360", "name": "Hades"}]

        def none(term):
            return []
        r = identity.resolve(game, {}, steam_search, none, lambda g: None)
        self.assertEqual((r["source"], r["id"], r["confidence"]), ("steam", "1145350", "title"))
        r = identity.resolve(game, {game["key"]: {"source": "pcgw", "id": "Hades II"}}, steam_search, none)
        self.assertEqual(r["confidence"], "confirmed")

        def two(term):
            return [{"source": "steam", "id": "1", "name": "Hades II"},
                    {"source": "steam", "id": "2", "name": "HADES II"}]
        r = identity.resolve(game, {}, two, none)
        self.assertTrue(r["needs_choice"])
        self.assertEqual(len(r["candidates"]), 2)

        gog = identity.merge([inst("gog", "The Witcher", "1207658924", ids={"gog": "1207658924"})])[0]
        r = identity.resolve(gog, {}, steam_search, none, lambda g: "The Witcher")
        self.assertEqual((r["source"], r["id"], r["confidence"]), ("pcgw", "The Witcher", "verified"))

    def test_resolve_reports_source_failures(self):
        from specbench import sources
        game = identity.merge([inst("epic", "Some Game", "s")])[0]

        def fail(term):
            raise sources.SourceError("down")
        r = identity.resolve(game, {}, fail, fail)
        self.assertTrue(r["needs_choice"])
        self.assertEqual(r["errors"], ["down", "down"])


if __name__ == "__main__":
    unittest.main()
