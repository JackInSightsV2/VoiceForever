from vo import questie
from vo.display import Displays

NPC_LUA = """-- AUTO GENERATED FILE! DO NOT EDIT!
QuestieDB.npcKeys = {
    ['name'] = 1, -- string
    ['spawns'] = 2, -- table
--    ['ignored'] = 3,
    ['zoneID'] = 3, -- int
}

QuestieDB.npcData = [[return {
[197] = {'Marshal McBride',{[12]={{48.92,41.61}}},12},
[300] = {'Guard',{[1]={{10.0,20.0}},[12]={{1.5,2.5},{3,4}}},1},
[301] = {'Nowhere',nil,nil},
}]]
"""

QUEST_LUA = """QuestieDB.questKeys = {
    ['name'] = 1, -- string
    ['startedBy'] = 2, -- table
        --['creatureStart'] = 1, -- table
    ['finishedBy'] = 3, -- table
    ['zoneOrSort'] = 4, -- int
}

QuestieDB.questData = [[return {
[783] = {'A Threat Within',{{823}},{{197}},9},
[805] = {'Letter',{nil,nil,{9000}},{{197},{68}},12},
}]]
"""


def test_load(tmp_path):
    (tmp_path / questie.FILES["npc"]).write_text(NPC_LUA)
    (tmp_path / questie.FILES["quest"]).write_text(QUEST_LUA)
    q = questie.load(tmp_path)
    assert q.npcs[197] == questie.Npc(zone=12, spawns={12: [(48.92, 41.61)]})
    assert q.npcs[300].spawns == {1: [(10.0, 20.0)], 12: [(1.5, 2.5), (3.0, 4.0)]}
    assert q.npcs[301] == questie.Npc(zone=None, spawns={})
    assert q.quests[783] == questie.Quest(creature_starts=[823], creature_ends=[197], zone_or_sort=9)
    assert q.quests[805].item_starts == [9000] and q.quests[805].object_ends == [68]


def test_displays_load_from_csv_and_listfile(tmp_path):
    files = {
        "CreatureDisplayInfo": "ID,ModelID,ExtendedDisplayInfoID,Gender\n1,5,0,2\n2,6,7,2\n",
        "CreatureDisplayInfoExtra": "ID,DisplayRaceID,DisplaySexID\n7,2,1\n",
        "CreatureModelData": "ID,FileDataID\n5,123020\n6,99\n",
        "ChrRaces": "ID,ClientFileString,Name_lang\n2,Orc,Orc\n",
    }
    for name, body in files.items():
        (tmp_path / f"{name}.csv").write_text(body)
    listfile = tmp_path / "listfile.csv"
    listfile.write_text("1;interface/x.avi\n123020;creature/basilisk/basilisk.m2\n")
    displays = Displays.load(tmp_path, listfile, server_gender={1: 1})
    assert displays.get(1).race == "Basilisk" and displays.get(1).gender == "female"  # VMaNGOS fallback
    assert (displays.get(2).race, displays.get(2).gender) == ("Orc", "female")
