Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
BaseDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = BaseDir
PythonW = FSO.BuildPath(BaseDir, ".venv\Scripts\pythonw.exe")
AgentPy = FSO.BuildPath(BaseDir, "agent.py")
WshShell.Run """" & PythonW & """ """ & AgentPy & """", 0, False
