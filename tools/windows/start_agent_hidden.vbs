Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ToolsDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BaseDir = FSO.GetAbsolutePathName(FSO.BuildPath(ToolsDir, "..\.."))
WshShell.CurrentDirectory = BaseDir
PythonExe = FSO.BuildPath(BaseDir, ".venv\Scripts\python.exe")
AgentPy = FSO.BuildPath(BaseDir, "app\agent.py")
WshShell.Run """" & PythonExe & """ """ & AgentPy & """", 0, False
