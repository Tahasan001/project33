#!/usr/bin/env python3
"""
Deployment helper script for ExamAssist
This script helps prepare the application for Vercel deployment
"""

import os
import sys
import subprocess
import secrets
from pathlib import Path

def generate_secret_key():
    """Generate a new Django secret key"""
    return secrets.token_urlsafe(50)

def check_requirements():
    """Check if all required files exist"""
    required_files = [
        'requirements.txt',
        'vercel.json',
        '.env.example',
        'build.sh'
    ]
    
    missing_files = []
    for file in required_files:
        if not Path(file).exists():
            missing_files.append(file)
    
    if missing_files:
        print(f"❌ Missing required files: {', '.join(missing_files)}")
        return False
    
    print("✅ All required files present")
    return True

def check_environment():
    """Check environment variables"""
    print("\n🔍 Checking environment variables...")
    
    # Check if .env exists
    if not Path('.env').exists():
        print("⚠️  .env file not found. Creating from .env.example...")
        if Path('.env.example').exists():
            with open('.env.example', 'r') as f:
                content = f.read()
            
            # Generate a new secret key
            new_secret_key = generate_secret_key()
            content = content.replace('your-secret-key-here', new_secret_key)
            
            with open('.env', 'w') as f:
                f.write(content)
            print("✅ .env file created with generated secret key")
        else:
            print("❌ .env.example file not found")
            return False
    else:
        print("✅ .env file exists")
    
    return True

def run_django_checks():
    """Run Django system checks"""
    print("\n🔍 Running Django system checks...")
    
    try:
        result = subprocess.run([
            sys.executable, 'manage.py', 'check', '--deploy'
        ], capture_output=True, text=True, check=True)
        
        print("✅ Django system checks passed")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Django system checks failed:")
        print(e.stdout)
        print(e.stderr)
        return False

def collect_static_files():
    """Collect static files"""
    print("\n📦 Collecting static files...")
    
    try:
        result = subprocess.run([
            sys.executable, 'manage.py', 'collectstatic', '--noinput'
        ], capture_output=True, text=True, check=True)
        
        print("✅ Static files collected successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to collect static files:")
        print(e.stderr)
        return False

def main():
    """Main deployment preparation function"""
    print("🚀 ExamAssist Deployment Preparation")
    print("=" * 40)
    
    # Check if we're in the right directory
    if not Path('manage.py').exists():
        print("❌ manage.py not found. Please run this script from the project root.")
        sys.exit(1)
    
    # Run checks
    if not check_requirements():
        sys.exit(1)
    
    if not check_environment():
        sys.exit(1)
    
    if not run_django_checks():
        sys.exit(1)
    
    if not collect_static_files():
        sys.exit(1)
    
    print("\n✅ Deployment preparation completed successfully!")
    print("\n📋 Next steps:")
    print("1. Push your code to GitHub")
    print("2. Deploy to Vercel (see DEPLOYMENT_GUIDE.md)")
    print("3. Set environment variables in Vercel dashboard")
    print("4. Test your deployed application")
    
    print(f"\n🔑 Generated secret key: {generate_secret_key()}")
    print("   (Use this for your SECRET_KEY environment variable)")

if __name__ == "__main__":
    main()
