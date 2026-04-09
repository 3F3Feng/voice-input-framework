#!/usr/bin/env python3
"""
Voice Input Framework - Model Selection Client

Change the STT model used by the server.

Usage:
    python change_model.py [--server HOST:PORT] [--model MODEL_NAME] [--list] [--info]

Examples:
    # List available models
    python change_model.py --list
    
    # Check current model
    python change_model.py --info
    
    # Switch to a specific model
    python change_model.py --model whisper
    python change_model.py --model qwen_asr
    
    # Connect to a remote server
    python change_model.py --server 192.168.1.100:6543 --model whisper
"""

import argparse
import asyncio
import sys
from typing import Optional

import httpx


class VoiceServerClient:
    """Voice Input Framework server client"""
    
    def __init__(self, host: str = "localhost", port: int = 6543, timeout: float = 10.0):
        """
        Initialize the client.
        
        Args:
            host: Server hostname or IP address
            port: Server port
            timeout: HTTP request timeout in seconds
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
    
    async def get_health(self) -> dict:
        """Get server health status and current model"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()
    
    async def list_models(self) -> list:
        """List all available models"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/models")
            response.raise_for_status()
            return response.json()
    
    async def select_model(self, model_name: str) -> dict:
        """Switch to a different model"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            data = {"model_name": model_name}
            response = await client.post(
                f"{self.base_url}/models/select",
                data=data
            )
            response.raise_for_status()
            return response.json()


async def main():
    parser = argparse.ArgumentParser(
        description="Change the STT model used by Voice Input Framework server"
    )
    parser.add_argument(
        "--server",
        type=str,
        default="localhost:6543",
        help="Server address in format HOST:PORT (default: localhost:6543)"
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model name to switch to"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available models"
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show current server and model info"
    )
    
    args = parser.parse_args()
    
    # Parse server address
    try:
        host, port_str = args.server.split(":")
        port = int(port_str)
    except (ValueError, IndexError):
        print(f"Error: Invalid server address format: {args.server}")
        print("Use format: HOST:PORT (e.g., localhost:6543)")
        sys.exit(1)
    
    client = VoiceServerClient(host=host, port=port)
    
    try:
        # Show info if no specific action requested
        if not args.model and not args.list:
            args.info = True
        
        # Get and display health/info
        if args.info:
            print("Fetching server info...")
            health = await client.get_health()
            print("\n" + "=" * 60)
            print("SERVER STATUS")
            print("=" * 60)
            print(f"Status:          {health.get('status', 'unknown')}")
            print(f"Version:         {health.get('version', 'unknown')}")
            print(f"Uptime:          {health.get('uptime_seconds', 0):.1f} seconds")
            print(f"Current Model:   {health.get('current_model', 'unknown')}")
            print(f"Loaded Models:   {', '.join(health.get('loaded_models', []))}")
            print("=" * 60 + "\n")
        
        # List available models
        if args.list:
            print("Fetching available models...")
            models = await client.list_models()
            print("\n" + "=" * 60)
            print("AVAILABLE MODELS")
            print("=" * 60)
            for model in models:
                current = " [CURRENT]" if model.get('is_default') else ""
                loaded = " [LOADED]" if model.get('is_loaded') else ""
                print(f"  • {model['name']:<20}{current}{loaded}")
                if model.get('description'):
                    print(f"    {model['description']}")
            print("=" * 60 + "\n")
        
        # Switch to selected model
        if args.model:
            print(f"Switching to model: {args.model}")
            result = await client.select_model(args.model)
            print(f"✓ Success! Current model: {result.get('current_model')}\n")
    
    except httpx.ConnectError as e:
        print(f"Error: Could not connect to server at {host}:{port}")
        print(f"Details: {e}")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"Error: Server returned {e.response.status_code}")
        print(f"Details: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
