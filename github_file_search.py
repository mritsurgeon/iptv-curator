#!/usr/bin/env python3
"""
GitHub File Searcher & Downloader
Searches GitHub for specific file types by extension (e.g., .m3u, .m3u8, .py, .json)
Supports:
- Direct GitHub Code Search API (with Token for global indexing)
- Smart Repository Tree Discovery (works unauthenticated out of the box!)
- Direct Raw File Downloader & Exporter
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

GITHUB_API_BASE = "https://api.github.com"

# SSL Context
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def build_search_query(
    extension: str,
    query: Optional[str] = None,
    filename: Optional[str] = None,
    repo: Optional[str] = None,
    user: Optional[str] = None,
    path: Optional[str] = None,
) -> str:
    """Builds a formatted GitHub Code Search query string."""
    parts = []
    
    if query:
        parts.append(query.strip())
    
    if extension:
        ext = extension.lstrip(".").strip()
        if ext:
            parts.append(f"extension:{ext}")
            
    if filename:
        parts.append(f"filename:{filename.strip()}")
        
    if repo:
        parts.append(f"repo:{repo.strip()}")
        
    if user:
        parts.append(f"user:{user.strip()}")
        
    if path:
        parts.append(f"path:{path.strip()}")
        
    return " ".join(parts)


def get_raw_url(html_url: str) -> str:
    """Converts a GitHub blob URL to its raw content URL."""
    return html_url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")


def make_request(url: str, token: Optional[str] = None) -> Any:
    """Makes a JSON request to GitHub API with proper headers."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHub-File-Searcher-CLI/2.0"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
        return json.loads(response.read().decode("utf-8"))


def search_via_code_api(
    query_str: str,
    token: str,
    max_results: int = 25
) -> List[Dict[str, Any]]:
    """Searches GitHub Code Search API using authentication token."""
    results = []
    page = 1
    
    print(f"🔑 Using GitHub Code Search API (Authenticated)...")
    while len(results) < max_results:
        params = {
            "q": query_str,
            "per_page": min(30, max_results - len(results)),
            "page": page,
            "sort": "indexed",
            "order": "desc"
        }
        url = f"{GITHUB_API_BASE}/search/code?{urllib.parse.urlencode(params)}"
        
        try:
            data = make_request(url, token=token)
            items = data.get("items", [])
            if not items:
                break
                
            for item in items:
                repo_info = item.get("repository", {})
                html_url = item.get("html_url", "")
                raw_url = get_raw_url(html_url)
                
                results.append({
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "repo_full_name": repo_info.get("full_name"),
                    "repo_url": repo_info.get("html_url"),
                    "repo_stars": repo_info.get("stargazers_count", 0),
                    "html_url": html_url,
                    "raw_url": raw_url
                })
                
                if len(results) >= max_results:
                    break
                    
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ Code API Search error: {e}")
            break
            
    return results


def search_via_repo_trees(
    extension: str,
    query: Optional[str] = None,
    repo_target: Optional[str] = None,
    user_target: Optional[str] = None,
    filename_filter: Optional[str] = None,
    token: Optional[str] = None,
    max_results: int = 25
) -> List[Dict[str, Any]]:
    """
    Finds repositories by topic/keywords and traverses Git trees to find files by extension.
    Works seamlessly without an API token for public repos!
    """
    ext = "." + extension.lstrip(".").lower()
    results = []
    
    # 1. Target a specific repo or search repositories
    target_repos = []
    if repo_target:
        try:
            repo_data = make_request(f"{GITHUB_API_BASE}/repos/{repo_target}", token=token)
            target_repos.append(repo_data)
        except Exception as e:
            print(f"❌ Could not access repo {repo_target}: {e}")
            return []
    elif user_target:
        try:
            user_repos = make_request(f"{GITHUB_API_BASE}/users/{user_target}/repos?per_page=30&sort=updated", token=token)
            target_repos.extend(user_repos)
        except Exception as e:
            print(f"❌ Could not access user repos for {user_target}: {e}")
            return []
    else:
        # Build repo search query
        search_terms = []
        if query:
            search_terms.append(query)
        search_terms.append(extension.lstrip("."))
        search_terms.append("stars:>1")
        
        repo_q = "+".join([urllib.parse.quote(term) for term in search_terms])
        repo_search_url = f"{GITHUB_API_BASE}/search/repositories?q={repo_q}&sort=stars&order=desc&per_page=20"
        
        print(f"🌐 Discovering top public repositories for query: '{query or ''}' ({ext})...")
        try:
            data = make_request(repo_search_url, token=token)
            target_repos = data.get("items", [])
            print(f"📦 Found {len(target_repos)} relevant repositories to inspect.")
        except Exception as e:
            print(f"❌ Repo discovery error: {e}")
            return []

    # 2. Inspect git trees for matching file extensions
    print(f"🌲 Scanning repository file trees for '*{ext}' files...")
    for repo in target_repos:
        if len(results) >= max_results:
            break
            
        full_name = repo.get("full_name")
        default_branch = repo.get("default_branch", "main")
        stars = repo.get("stargazers_count", 0)
        
        tree_url = f"{GITHUB_API_BASE}/repos/{full_name}/git/trees/{default_branch}?recursive=1"
        try:
            tree_data = make_request(tree_url, token=token)
            tree_items = tree_data.get("tree", [])
            
            for item in tree_items:
                if item.get("type") == "blob":
                    path = item.get("path", "")
                    if path.lower().endswith(ext):
                        fname = os.path.basename(path)
                        
                        # Filename filter if specified
                        if filename_filter and filename_filter.lower() not in fname.lower():
                            continue
                            
                        html_url = f"https://github.com/{full_name}/blob/{default_branch}/{path}"
                        raw_url = f"https://raw.githubusercontent.com/{full_name}/{default_branch}/{path}"
                        
                        results.append({
                            "name": fname,
                            "path": path,
                            "repo_full_name": full_name,
                            "repo_url": repo.get("html_url"),
                            "repo_stars": stars,
                            "html_url": html_url,
                            "raw_url": raw_url
                        })
                        
                        if len(results) >= max_results:
                            break
                            
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"⚠️ Rate limit reached on tree scan. Pass a GitHub Token via --token or GITHUB_TOKEN for higher limits.")
                break
        except Exception:
            continue
            
    return results


def download_file(url: str, output_path: str) -> bool:
    """Downloads a single file from URL, handling international/Unicode characters."""
    try:
        encoded_url = urllib.parse.quote(url, safe=":/%?&=#+@~_-")
        req = urllib.request.Request(encoded_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            content = resp.read()
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(content)
        return True
    except Exception as e:
        print(f"    ❌ Failed to download {url}: {e}")
        return False


def download_all_files_concurrently(results: List[Dict[str, Any]], dl_dir: str, max_workers: int = 15) -> int:
    """Downloads files concurrently using ThreadPoolExecutor."""
    import concurrent.futures
    os.makedirs(dl_dir, exist_ok=True)
    print(f"\n📥 Downloading {len(results)} files to '{dl_dir}' using {max_workers} threads...")
    
    tasks = []
    for item in results:
        clean_repo = item['repo_full_name'].replace("/", "_")
        clean_path = item['path'].replace("/", "_")
        fname = f"{clean_repo}_{clean_path}"
        target = os.path.join(dl_dir, fname)
        tasks.append((item['raw_url'], target, fname))
        
    success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {executor.submit(download_file, url, target): (fname, url) for url, target, fname in tasks}
        for future in concurrent.futures.as_completed(future_to_task):
            fname, _ = future_to_task[future]
            try:
                if future.result():
                    success += 1
                    print(f"  ✅ Downloaded: {fname}")
            except Exception as e:
                print(f"  ❌ Error downloading {fname}: {e}")
                
    return success


def main():
    parser = argparse.ArgumentParser(
        description="Search GitHub for files by file extension, keywords, and repository filters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for M3U playlist files mentioning 'sports':
  python3 github_file_search.py --ext m3u --query sports

  # Search for M3U8 files and download them into a local folder:
  python3 github_file_search.py --ext m3u8 --query iptv --download ./downloads

  # Search inside a specific repository:
  python3 github_file_search.py --ext m3u --repo iptv-org/iptv

  # Search with a GitHub Personal Access Token (for deeper indexed code search):
  python3 github_file_search.py --ext py --query stream --token ghp_yourTokenHere --limit 30
        """
    )
    
    parser.add_argument("-e", "--ext", "--extension", dest="extension", type=str, help="File extension to search for (e.g. m3u, m3u8, py, json, yaml)")
    parser.add_argument("-q", "--query", type=str, help="Keyword query or search topic")
    parser.add_argument("-f", "--filename", type=str, help="Specific filename or keyword inside the filename")
    parser.add_argument("-r", "--repo", type=str, help="Target a specific repository (e.g. iptv-org/iptv)")
    parser.add_argument("-u", "--user", type=str, help="Target a specific user or organization")
    parser.add_argument("-p", "--path", type=str, help="Target a specific path prefix")
    parser.add_argument("-l", "--limit", type=int, default=20, help="Maximum results to return (default: 20)")
    parser.add_argument("-t", "--token", type=str, default=os.getenv("GITHUB_TOKEN"), help="GitHub Personal Access Token (or GITHUB_TOKEN env var)")
    parser.add_argument("-o", "--output", type=str, help="Save result links to a file (.json or .txt)")
    parser.add_argument("-d", "--download", type=str, help="Directory to download all matched raw files into")
    
    args = parser.parse_args()
    
    # Interactive mode if no arguments provided
    if not args.extension and not args.query and not args.repo and not args.filename:
        print("=== 🔎 GitHub File Searcher ===")
        ext_input = input("Enter file extension to search (e.g. m3u, m3u8, py, json): ").strip()
        query_input = input("Enter keyword/topic (e.g. iptv, sports, stream - or press Enter): ").strip()
        download_input = input("Download matched files to directory? (optional, press Enter to skip): ").strip()
        token_input = input("GitHub Token (optional, press Enter to skip): ").strip()
        
        args.extension = ext_input if ext_input else "m3u"
        args.query = query_input if query_input else None
        if download_input:
            args.download = download_input
        if token_input:
            args.token = token_input

    if not args.extension:
        print("❌ Error: You must specify a file extension (-e / --ext). Example: -e m3u")
        sys.exit(1)

    print(f"\n🚀 Starting search for *.{args.extension.lstrip('.')} files (Query: '{args.query or 'any'}')...\n")
    
    effective_limit = args.limit if (args.limit and args.limit > 0) else 999999

    results = []
    # If token is provided, attempt Code Search API first
    if args.token:
        code_query = build_search_query(
            extension=args.extension,
            query=args.query,
            filename=args.filename,
            repo=args.repo,
            user=args.user,
            path=args.path
        )
        results = search_via_code_api(code_query, token=args.token, max_results=effective_limit)

    # Fallback to Repo Tree Discovery if no token or Code API returned 0
    if not results:
        results = search_via_repo_trees(
            extension=args.extension,
            query=args.query,
            repo_target=args.repo,
            user_target=args.user,
            filename_filter=args.filename,
            token=args.token,
            max_results=effective_limit
        )

    if not results:
        print("\n⚠️ No files found matching your search criteria.")
        return

    print(f"\n✨ Found {len(results)} matching files:\n" + "=" * 80)
    for idx, item in enumerate(results, 1):
        stars_info = f" ⭐ {item['repo_stars']}" if item.get("repo_stars") else ""
        print(f"[{idx}] 📁 {item['repo_full_name']}{stars_info}")
        print(f"    📄 File: {item['path']}")
        print(f"    🔗 Raw:  {item['raw_url']}")
        print("-" * 80)

    # Export to JSON or TXT
    if args.output:
        out_path = args.output
        if out_path.endswith(".json"):
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            print(f"\n💾 Saved structured JSON to: {out_path}")
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                for item in results:
                    f.write(f"{item['raw_url']}\n")
            print(f"\n💾 Saved raw URLs list to: {out_path}")

    # Direct File Downloader
    if args.download:
        success = download_all_files_concurrently(results, args.download, max_workers=15)
        print(f"\n🎉 Done! Successfully downloaded {success}/{len(results)} files into '{args.download}'.")


if __name__ == "__main__":
    main()
