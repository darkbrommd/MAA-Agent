Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ToolsDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BaseDir = FSO.GetAbsolutePathName(FSO.BuildPath(ToolsDir, "..\.."))
WshShell.CurrentDirectory = BaseDir
PythonW = FSO.BuildPath(BaseDir, ".venv\Scripts\pythonw.exe")
AgentPy = FSO.BuildPath(BaseDir, "app\agent.py")
WshShell.Run """" & PythonW & """ """ & AgentPy & """", 0, False
