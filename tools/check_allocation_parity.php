<?php
declare(strict_types=1);

// Called only by the offline reviewer tool with an explicitly trusted checkout.
if ($argc !== 2) {
    throw new InvalidArgumentException('One Symfony checkout path is required.');
}
require $argv[1] . '/vendor/autoload.php';
use App\Service\BalancedWorldAllocation;

$cases = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
$out = [];
foreach ($cases as $case) {
    $out[] = [
        'allocation' => BalancedWorldAllocation::allocateRoster($case['seed'], $case['readers'], $case['forms'], $case['per_cell']),
        'commitment' => BalancedWorldAllocation::commitment($case['seed'], $case['readers'], $case['forms'], $case['per_cell']),
    ];
}
echo json_encode($out, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
