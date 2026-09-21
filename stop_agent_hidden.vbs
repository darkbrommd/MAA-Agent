Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
BaseDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = BaseDir
PythonW = FSO.BuildPath(BaseDir, ".venv\Scripts\pythonw.exe")
StopPy = FSO.BuildPath(BaseDir, "stop_agent.py")
WshShell.Run """" & PythonW & """ """ & StopPy & """ --quiet", 0, False
