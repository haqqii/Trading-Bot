import os
import sys

# Change to script directory
os.chdir(r"D:\Haqqi\Proyek Mini\Bot Saham 2")

# Set UTF-8
if sys.platform == 'win32':
    os.system('chcp 65001 >nul')

# Run main
sys.path.insert(0, r"D:\Haqqi\Proyek Mini\Bot Saham 2")
import main
main.main()
