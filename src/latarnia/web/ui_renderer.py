"""
UI Renderer for Service App REST APIs

This module handles rendering of service app UIs by discovering their /ui endpoint
and dynamically rendering tables, forms, and other UI elements based on the REST API responses.
"""
import logging
from typing import Dict, List, Any, Optional
import httpx
from datetime import datetime


class UIRenderer:
    """Renders service app UIs from REST API endpoints"""
    
    def __init__(self):
        self.logger = logging.getLogger("latarnia.ui_renderer")
    
    async def discover_ui_resources(self, base_url: str,
                                    headers: Optional[Dict[str, str]] = None) -> Optional[List[str]]:
        """
        Discover available UI resources from a service app's /ui endpoint
        
        Args:
            base_url: Base URL of the service app (e.g., http://localhost:8100)
            headers: Optional request headers (the caller's X-Latarnia-* identity)
            
        Returns:
            List of resource names or None if /ui endpoint doesn't exist
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{base_url}/ui", headers=headers, timeout=5.0)
                if response.status_code == 200:
                    resources = response.json()
                    if isinstance(resources, list):
                        self.logger.info(f"Discovered UI resources from {base_url}: {resources}")
                        return resources
                return None
        except Exception as e:
            self.logger.debug(f"No /ui endpoint found at {base_url}: {e}")
            return None
    
    async def fetch_resource_list(self, base_url: str, resource: str,
                                  headers: Optional[Dict[str, str]] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch a list of items from a resource endpoint
        
        Args:
            base_url: Base URL of the service app
            resource: Resource name (e.g., "messages", "files")
            headers: Optional request headers (the caller's X-Latarnia-* identity)
            
        Returns:
            List of resource items or None on error
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{base_url}/api/{resource}", headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return data
                return None
        except Exception as e:
            self.logger.error(f"Failed to fetch {resource} from {base_url}: {e}")
            return None
    
    async def fetch_resource_detail(self, base_url: str, resource: str, item_id: Any,
                                    headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch details for a specific resource item
        
        Args:
            base_url: Base URL of the service app
            resource: Resource name
            item_id: ID of the specific item
            headers: Optional request headers (the caller's X-Latarnia-* identity)
            
        Returns:
            Resource item details or None on error
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{base_url}/api/{resource}/{item_id}", headers=headers, timeout=10.0)
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception as e:
            self.logger.error(f"Failed to fetch {resource}/{item_id} from {base_url}: {e}")
            return None
    
    def render_table_html(self, data: List[Dict[str, Any]], resource_name: str) -> str:
        """
        Render a list of items as an HTML table
        
        Args:
            data: List of resource items
            resource_name: Name of the resource for display
            
        Returns:
            HTML string for the table
        """
        if not data:
            return f'<div class="alert alert-info">No {resource_name} found</div>'
        
        # Get all unique keys from all items
        all_keys = set()
        for item in data:
            all_keys.update(item.keys())
        
        # Sort keys, put 'id' first if it exists
        keys = sorted(all_keys)
        if 'id' in keys:
            keys.remove('id')
            keys.insert(0, 'id')
        
        # Build table HTML
        html = f'<div class="table-responsive"><table class="table table-sm table-hover">'
        html += '<thead class="table-light"><tr>'
        
        for key in keys:
            display_name = key.replace('_', ' ').title()
            html += f'<th>{display_name}</th>'
        
        html += '</tr></thead><tbody>'
        
        for item in data:
            item_id = item.get('id', '')
            html += f'<tr data-item-id="{item_id}" style="cursor:pointer">'
            for key in keys:
                value = item.get(key, '')
                formatted_value = self._format_value(key, value)
                html += f'<td>{formatted_value}</td>'
            html += '</tr>'
        
        html += '</tbody></table></div>'
        return html
    
    def render_detail_html(self, data: Dict[str, Any], resource_name: str) -> str:
        """Render a single resource item as an HTML detail view."""
        if not data:
            return f'<div class="alert alert-info">No {resource_name} detail found</div>'

        html = '<div class="card"><div class="card-body">'
        html += '<dl class="row mb-0">'

        for key, value in data.items():
            display_name = key.replace('_', ' ').title()
            # Render nested lists (e.g. events, tags) as sub-tables
            if isinstance(value, list) and value and isinstance(value[0], dict):
                formatted = self.render_table_html(value, key)
            elif isinstance(value, list):
                formatted = ', '.join(str(v) for v in value) if value else '<span class="text-muted">None</span>'
            else:
                formatted = self._format_value(key, value)
            html += f'<dt class="col-sm-3">{display_name}</dt><dd class="col-sm-9">{formatted}</dd>'

        html += '</dl></div></div>'
        return html

    def _format_value(self, key: str, value: Any) -> str:
        """Format a value for display based on its key name and type"""
        if value is None:
            return '<span class="text-muted">N/A</span>'
        
        # Handle date fields (unix timestamp or ISO string)
        if 'date' in key.lower() or 'time' in key.lower() or key.endswith('_at'):
            if isinstance(value, (int, float)):
                # Unix timestamp
                try:
                    dt = datetime.fromtimestamp(value)
                    return dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    return str(value)
            elif isinstance(value, str):
                # Try to parse ISO date
                try:
                    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    return dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    return value
        
        # Handle image fields
        if key.startswith('img_') or key.endswith('_image') or key == 'image':
            if isinstance(value, str) and (value.startswith('http') or value.startswith('data:')):
                return f'<img src="{value}" alt="{key}" style="max-width: 100px; max-height: 100px;" class="img-thumbnail">'
        
        # Handle boolean
        if isinstance(value, bool):
            return '✅' if value else '❌'
        
        # Handle arrays
        if isinstance(value, list):
            if len(value) == 0:
                return '<span class="text-muted">Empty</span>'
            return f'<span class="badge bg-secondary">{len(value)} items</span>'
        
        # Handle objects
        if isinstance(value, dict):
            return f'<span class="badge bg-info">{len(value)} fields</span>'
        
        # Default: convert to string and truncate if too long
        str_value = str(value)
        if len(str_value) > 100:
            return f'<span title="{str_value}">{str_value[:100]}...</span>'
        
        return str_value


# Global instance
ui_renderer = UIRenderer()
