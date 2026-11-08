Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ToolsDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BaseDir = FSO.GetAbsolutePathName(FSO.BuildPath(ToolsDir, "..\.."))
WshShell.CurrentDirectory = BaseDir
PythonW = FSO.BuildPath(BaseDir, ".venv\Scripts\pythonw.exe")
StopPy = FSO.BuildPath(BaseDir, "app\stop_agent.py")
WshShell.Run """" & PythonW & """ """ & StopPy & """ --quiet", 0, False
