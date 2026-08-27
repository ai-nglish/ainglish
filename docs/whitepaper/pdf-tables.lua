-- The gfm reader records no column widths, so pandoc's LaTeX writer emits
-- non-wrapping l/c/r columns and wide generated tables run off the page. Derive
-- relative widths from the longest cell in each column, capped so that one very
-- long identifier cannot starve its neighbours.
local CAP = 46

function Table(tbl)
  local n = #tbl.colspecs
  if n == 0 then return nil end
  local w = {}
  for i = 1, n do w[i] = 1 end
  local function measure(rows)
    for _, row in ipairs(rows) do
      for i, cell in ipairs(row.cells) do
        if i <= n then
          local len = #pandoc.utils.stringify(cell.contents)
          if len > w[i] then w[i] = len end
        end
      end
    end
  end
  measure(tbl.head.rows)
  for _, body in ipairs(tbl.bodies) do measure(body.body) end
  local total = 0
  for i = 1, n do
    if w[i] > CAP then w[i] = CAP end
    total = total + w[i]
  end
  for i = 1, n do
    tbl.colspecs[i] = { tbl.colspecs[i][1], w[i] / total }
  end
  return tbl
end
